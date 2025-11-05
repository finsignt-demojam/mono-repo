"""
FinSight Audio Event Handler

Flask application that receives S3 events from Knative Broker
and triggers the audio transcription Kubeflow Pipeline.

Event Flow:
1. MinIO S3 ObjectCreated event → Kafka
2. KafkaSource → Knative Broker
3. Trigger → This Event Handler
4. Event Handler → Kubeflow Pipeline Execution
"""

import os
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from flask import Flask, request, jsonify
from cloudevents.http import from_http
from urllib.parse import unquote, urljoin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration from environment variables
KFP_ENDPOINT = os.getenv('KFP_ENDPOINT', 'https://ml-pipeline.finsight-agent.svc.cluster.local:8888')
KFP_NAMESPACE = os.getenv('KFP_NAMESPACE', 'finsight-agent')
PIPELINE_NAME = os.getenv('PIPELINE_NAME', 'audio-transcription-pipeline')
EXPERIMENT_NAME = os.getenv('EXPERIMENT_NAME', 'Audio Transcription Runs')
KFP_VERIFY_SSL = os.getenv('KFP_VERIFY_SSL', 'false').lower() in ('true', '1', 'yes', 'y')
KFP_SSL_CA_CERT = os.getenv('KFP_SSL_CA_CERT')
KFP_REQUEST_TIMEOUT = float(os.getenv('KFP_REQUEST_TIMEOUT', '60'))

# MinIO Configuration
MINIO_ENDPOINT = os.getenv('MINIO_ENDPOINT', 'http://minio.minio.svc.cluster.local:9000')
MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY', 'minioadmin123')

# Voxtral Configuration
VOXTRAL_API_URL = os.getenv('VOXTRAL_API_URL', 'https://api.scaleway.ai/v1')
VOXTRAL_API_KEY = os.getenv('VOXTRAL_API_KEY', '')
VOXTRAL_MODEL = os.getenv('VOXTRAL_MODEL', 'voxtral-small-24b-2507')

# Milvus Configuration
MILVUS_HOST = os.getenv('MILVUS_HOST', 'milvus.finsight-agent.svc.cluster.local')
MILVUS_PORT = os.getenv('MILVUS_PORT', '19530')
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'earnings_call_transcripts')

_kfp_session: Optional[requests.Session] = None
_cached_pipeline_id: Optional[str] = None
_cached_experiment_id: Optional[str] = None


def _get_kfp_session() -> requests.Session:
    global _kfp_session
    if _kfp_session is None:
        session = requests.Session()

        if not KFP_VERIFY_SSL:
            session.verify = False
        elif KFP_SSL_CA_CERT:
            session.verify = KFP_SSL_CA_CERT

        session.headers.update({'Content-Type': 'application/json'})
        _kfp_session = session

    return _kfp_session


def _kfp_request(method: str, path: str, **kwargs) -> Dict[str, Any]:
    url = urljoin(KFP_ENDPOINT.rstrip('/') + '/', path.lstrip('/'))
    timeout = kwargs.pop('timeout', KFP_REQUEST_TIMEOUT)

    session = _get_kfp_session()
    response = session.request(method.upper(), url, timeout=timeout, **kwargs)

    if response.status_code >= 400:
        logger.error(
            "KFP API call failed",
            extra={
                'method': method,
                'url': url,
                'status_code': response.status_code,
                'response': response.text,
            }
        )
        raise RuntimeError(
            f"KFP API {method.upper()} {url} failed: {response.status_code} - {response.text}"
        )

    if not response.content:
        return {}

    try:
        return response.json()
    except ValueError:
        logger.warning("Failed to decode JSON response from KFP", extra={'url': url})
        return {}


def _build_filter(display_name: str) -> str:
    filter_payload = {
        "predicates": [
            {
                "key": "display_name",
                "operation": 1,  # EQUALS
                "stringValue": display_name,
            }
        ]
    }
    return json.dumps(filter_payload)


def _get_or_create_experiment_id() -> str:
    global _cached_experiment_id
    if _cached_experiment_id:
        return _cached_experiment_id

    params = {
        'namespace': KFP_NAMESPACE,
        'filter': _build_filter(EXPERIMENT_NAME),
        'page_size': 1,
    }

    experiments = _kfp_request('GET', '/apis/v2beta1/experiments', params=params)
    items = experiments.get('experiments') or []
    if items:
        _cached_experiment_id = items[0].get('experiment_id')
        logger.info(f"Using existing experiment: {EXPERIMENT_NAME} ({_cached_experiment_id})")
        return _cached_experiment_id

    payload = {
        'display_name': EXPERIMENT_NAME,
        'namespace': KFP_NAMESPACE,
        'description': f'Auto-created by FinSight handler at {datetime.utcnow().isoformat()}Z',
    }

    created = _kfp_request('POST', '/apis/v2beta1/experiments', json=payload)
    experiment_id = created.get('experiment_id')
    if not experiment_id:
        raise RuntimeError('Failed to create or retrieve experiment ID from KFP')

    _cached_experiment_id = experiment_id
    logger.info(f"Created experiment: {EXPERIMENT_NAME} ({experiment_id})")
    return experiment_id


def _get_pipeline_id() -> str:
    global _cached_pipeline_id
    if _cached_pipeline_id:
        return _cached_pipeline_id

    params = {
        'namespace': KFP_NAMESPACE,
        'page_size': 200,
    }

    response = _kfp_request('GET', '/apis/v2beta1/pipelines', params=params)
    for pipeline in response.get('pipelines', []):
        display_name = pipeline.get('display_name') or pipeline.get('name')
        if display_name == PIPELINE_NAME:
            pipeline_id = pipeline.get('pipeline_id') or pipeline.get('pipelineVersionId')
            if pipeline_id:
                _cached_pipeline_id = pipeline_id
                logger.info(f"Resolved pipeline '{PIPELINE_NAME}' to ID {pipeline_id}")
                return pipeline_id

    raise RuntimeError(f"Pipeline '{PIPELINE_NAME}' not found in namespace '{KFP_NAMESPACE}'")


def trigger_pipeline(bucket_name: str, object_key: str, event_time: str):
    """
    Trigger the audio transcription pipeline with the given parameters
    
    Args:
        bucket_name: S3 bucket name
        object_key: S3 object key (file path)
        event_time: Event timestamp
    """
    try:
        experiment_id = _get_or_create_experiment_id()
        pipeline_id = _get_pipeline_id()

        pipeline_params = {
            's3_bucket': bucket_name,
            's3_key': object_key,
            's3_endpoint_url': MINIO_ENDPOINT,
            's3_access_key': MINIO_ACCESS_KEY,
            's3_secret_key': MINIO_SECRET_KEY,
            'voxtral_api_url': VOXTRAL_API_URL,
            'voxtral_api_key': VOXTRAL_API_KEY,
            'voxtral_model': VOXTRAL_MODEL,
            'milvus_host': MILVUS_HOST,
            'milvus_port': MILVUS_PORT,
            'collection_name': COLLECTION_NAME
        }
        
        # Generate run name with timestamp
        run_name = f"audio-{object_key.replace('/', '-')}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        payload = {
            'display_name': run_name,
            'pipeline_version_reference': {
                'pipeline_id': pipeline_id,
            },
            'runtime_config': {
                'parameters': pipeline_params,
            },
        }

        response = _kfp_request(
            'POST',
            '/apis/v2beta1/runs',
            params={'experiment_id': experiment_id},
            json=payload,
            timeout=max(KFP_REQUEST_TIMEOUT, 90),
        )

        run_id = response.get('run_id')

        logger.info(f"✅ Pipeline run created: {run_name}")
        if run_id:
            logger.info(f"   Run ID: {run_id}")
        logger.info(f"   Bucket: {bucket_name}")
        logger.info(f"   Object: {object_key}")

        return response
        
    except Exception as e:
        logger.error(f"Failed to trigger pipeline: {e}", exc_info=True)
        raise


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'audio-event-handler',
        'kfp_endpoint': KFP_ENDPOINT
    }), 200


@app.route('/ready', methods=['GET'])
def ready():
    """Readiness probe endpoint"""
    return jsonify({'status': 'ready'}), 200


@app.route('/', methods=['POST'])
def handle_event():
    """
    Handle CloudEvents from Knative Broker
    
    Expects CloudEvent with S3 event data in the data field
    """
    try:
        # Parse CloudEvent
        event = from_http(request.headers, request.get_data())
        
        logger.info(f"📨 Received CloudEvent:")
        logger.info(f"   Type: {event['type']}")
        logger.info(f"   Source: {event['source']}")
        logger.info(f"   Subject: {event.get('subject', 'N/A')}")
        
        # Extract S3 event data
        event_data = event.data
        
        if not event_data:
            logger.warning("No data in CloudEvent")
            return jsonify({'status': 'ignored', 'reason': 'no data'}), 200
        
        # Parse S3 event structure (MinIO format)
        # MinIO sends events with 'Records' array
        records = event_data.get('Records', [])
        
        if not records:
            # Try direct structure
            bucket_name = event_data.get('bucket', {}).get('name')
            object_key = event_data.get('object', {}).get('key')
            event_time = event_data.get('eventTime', datetime.now().isoformat())
        else:
            # Standard S3 event format
            record = records[0]
            s3_info = record.get('s3', {})
            bucket_name = s3_info.get('bucket', {}).get('name')
            object_key = s3_info.get('object', {}).get('key')
            event_time = record.get('eventTime', datetime.now().isoformat())
        
        if not bucket_name or not object_key:
            logger.warning(f"Missing bucket or object key in event data: {event_data}")
            return jsonify({'status': 'ignored', 'reason': 'invalid event structure'}), 200

        object_key = unquote(object_key)
        
        # Only process audio files in audio-inbox bucket
        if bucket_name != 'audio-inbox':
            logger.info(f"Ignoring event from bucket: {bucket_name}")
            return jsonify({'status': 'ignored', 'reason': f'bucket {bucket_name} not monitored'}), 200
        
        # Skip pipeline output objects to avoid retriggering on transcripts or segments
        if object_key.lower().startswith('transcripts/'):
            logger.info(f"Ignoring pipeline output object: {object_key}")
            return jsonify({'status': 'ignored', 'reason': 'transcript artifacts are not ingested'}), 200

        # Only process audio files
        audio_extensions = ['.mp3', '.wav', '.m4a', '.flac', '.ogg']
        if not any(object_key.lower().endswith(ext) for ext in audio_extensions):
            logger.info(f"Ignoring non-audio file: {object_key}")
            return jsonify({'status': 'ignored', 'reason': 'not an audio file'}), 200
        
        logger.info(f"🎵 Processing audio file: s3://{bucket_name}/{object_key}")
        
        # Trigger pipeline
        run = trigger_pipeline(bucket_name, object_key, event_time)
        
        if run:
            run_id = run.get('run_id')
            return jsonify({
                'status': 'success',
                'message': 'Pipeline triggered',
                'run_id': run_id,
                'bucket': bucket_name,
                'object': object_key
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to trigger pipeline'
            }), 500
        
    except Exception as e:
        logger.error(f"Error handling event: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


if __name__ == '__main__':
    logger.info("🚀 Starting FinSight Audio Event Handler")
    logger.info(f"   KFP Endpoint: {KFP_ENDPOINT}")
    logger.info(f"   Pipeline: {PIPELINE_NAME}")
    logger.info(f"   Experiment: {EXPERIMENT_NAME}")
    
    # Run Flask app
    port = int(os.getenv('PORT', '8080'))
    app.run(host='0.0.0.0', port=port)

