"""
Transcription Component

Provides Voxtral API integration for audio-to-text transcription.
Supports both Scaleway GenAI API and self-hosted vLLM endpoints.
"""

import base64
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path
from openai import OpenAI
from tqdm import tqdm
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def encode_audio_file_to_base64(file_path: str) -> str:
    """
    Encode audio file directly to base64 for API transmission.
    More efficient for saved segment files.
    
    Args:
        file_path: Path to the audio file
        
    Returns:
        Base64 encoded string of the audio file
        
    Raises:
        FileNotFoundError: If file doesn't exist
        RuntimeError: If encoding fails
    """
    try:
        with open(file_path, 'rb') as audio_file:
            audio_data = audio_file.read()
            return base64.b64encode(audio_data).decode('utf-8')
    except FileNotFoundError:
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    except Exception as e:
        raise RuntimeError(f"Error encoding audio file to base64: {e}")


def transcribe_audio_segment(
    client: OpenAI,
    segment: Dict[str, Any],
    model: str = "voxtral-small-24b-2507",
    max_retries: int = 3,
    retry_delay: float = 2.0
) -> Optional[Dict[str, Any]]:
    """
    Transcribe a single audio segment using Voxtral API.
    
    Args:
        client: OpenAI client configured for Voxtral endpoint
        segment: Segment metadata dictionary containing 'path' key
        model: Model name for transcription
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
        
    Returns:
        Dictionary containing transcription result or None if failed
        {
            'segment_id': int,
            'text': str,
            'start_time': float,
            'end_time': float,
            'duration': float,
            'success': bool,
            'error': Optional[str]
        }
    """
    segment_id = segment.get('segment_id', 0)
    segment_path = segment.get('path', '')
    
    for attempt in range(max_retries):
        try:
            # Encode audio to base64
            encoded_audio = encode_audio_file_to_base64(segment_path)
            
            # Call Voxtral API (OpenAI-compatible)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "audio",
                                "audio": encoded_audio
                            }
                        ]
                    }
                ],
                temperature=0.0,  # Deterministic transcription
                max_tokens=2048
            )
            
            # Extract transcription text
            transcription_text = response.choices[0].message.content
            
            return {
                'segment_id': segment_id,
                'text': transcription_text,
                'start_time': segment.get('start_time', 0.0),
                'end_time': segment.get('end_time', 0.0),
                'duration': segment.get('duration', 0.0),
                'filename': segment.get('filename', ''),
                'success': True,
                'error': None
            }
            
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for segment {segment_id}: {e}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
            else:
                logger.error(f"All retries exhausted for segment {segment_id}")
                return {
                    'segment_id': segment_id,
                    'text': '',
                    'start_time': segment.get('start_time', 0.0),
                    'end_time': segment.get('end_time', 0.0),
                    'duration': segment.get('duration', 0.0),
                    'filename': segment.get('filename', ''),
                    'success': False,
                    'error': str(e)
                }
    
    return None


def batch_transcribe_segments(
    api_url: str,
    api_key: str,
    segments: List[Dict[str, Any]],
    model: str = "voxtral-small-24b-2507",
    max_retries: int = 3,
    show_progress: bool = True
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Transcribe multiple audio segments in batch.
    
    Args:
        api_url: Voxtral API endpoint URL (e.g., Scaleway or vLLM)
        api_key: API authentication key
        segments: List of segment metadata dictionaries
        model: Model name for transcription
        max_retries: Maximum retry attempts per segment
        show_progress: Whether to show progress bar
        
    Returns:
        Tuple of (successful_transcriptions, failed_transcriptions)
    """
    logger.info(f"Starting batch transcription of {len(segments)} segments")
    logger.info(f"API URL: {api_url}")
    logger.info(f"Model: {model}")

    # Demo / stub mode when no API key provided
    if not api_key:
        logger.warning(
            "No Voxtral API key supplied. Running in demo mode with stubbed "
            "transcriptions (no external calls will be made)."
        )

        successful_transcriptions: List[Dict[str, Any]] = []
        for segment in segments:
            segment_id = segment.get('segment_id', 0)
            filename = segment.get('filename') or Path(segment.get('path', '')).name
            start_time = segment.get('start_time', 0.0)
            end_time = segment.get('end_time', start_time + segment.get('duration', 0.0))
            duration = segment.get('duration') or max(0.0, end_time - start_time)

            demo_text = (
                f"[DEMO TRANSCRIPT] Segment {segment_id + 1} from file '{filename}'. "
                "This is generated placeholder text to illustrate the final "
                "experience without calling the Voxtral API."
            )

            successful_transcriptions.append({
                'segment_id': segment_id,
                'text': demo_text,
                'start_time': start_time,
                'end_time': end_time,
                'duration': duration,
                'filename': filename,
                'success': True,
                'error': None
            })

        logger.info(
            "Demo transcription complete: %d successful, 0 failed",
            len(successful_transcriptions)
        )
        return successful_transcriptions, []

    # Initialize OpenAI client for real transcriptions
    client = OpenAI(
        base_url=api_url,
        api_key=api_key
    )

    successful_transcriptions: List[Dict[str, Any]] = []
    failed_transcriptions: List[Dict[str, Any]] = []

    # Transcribe segments with progress tracking
    iterator = tqdm(segments, desc="Transcribing segments") if show_progress else segments

    for segment in iterator:
        result = transcribe_audio_segment(
            client=client,
            segment=segment,
            model=model,
            max_retries=max_retries
        )

        if result and result['success']:
            successful_transcriptions.append(result)
        else:
            failed_transcriptions.append(result or segment)

    logger.info(
        "Transcription complete: %d successful, %d failed",
        len(successful_transcriptions),
        len(failed_transcriptions)
    )

    return successful_transcriptions, failed_transcriptions


def create_complete_transcript(
    transcriptions: List[Dict[str, Any]],
    output_path: Optional[Path] = None
) -> str:
    """
    Create a complete transcript from individual segment transcriptions.
    
    Args:
        transcriptions: List of transcription result dictionaries
        output_path: Optional path to save the transcript file
        
    Returns:
        Complete transcript as a string
    """
    # Sort by segment_id to ensure correct order
    sorted_transcriptions = sorted(transcriptions, key=lambda x: x['segment_id'])
    
    # Build transcript with timestamps
    transcript_lines = []
    transcript_lines.append("=== EARNINGS CALL TRANSCRIPT ===\n")
    
    for trans in sorted_transcriptions:
        start_time = trans['start_time']
        end_time = trans['end_time']
        text = trans['text'].strip()
        
        # Format timestamp
        start_min = int(start_time // 60)
        start_sec = int(start_time % 60)
        end_min = int(end_time // 60)
        end_sec = int(end_time % 60)
        
        timestamp = f"[{start_min:02d}:{start_sec:02d} - {end_min:02d}:{end_sec:02d}]"
        
        transcript_lines.append(f"\n{timestamp}")
        transcript_lines.append(text)
    
    # Add summary footer
    transcript_lines.append("\n\n=== END TRANSCRIPT ===")
    transcript_lines.append(f"\nTotal segments: {len(sorted_transcriptions)}")
    
    if sorted_transcriptions:
        total_duration = sorted_transcriptions[-1]['end_time']
        transcript_lines.append(f"Total duration: {int(total_duration // 60)}:{int(total_duration % 60):02d}")
    
    complete_transcript = '\n'.join(transcript_lines)
    
    # Save to file if path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(complete_transcript)
        
        logger.info(f"Complete transcript saved to: {output_path}")
    
    return complete_transcript


def create_transcript_metadata(
    transcriptions: List[Dict[str, Any]],
    audio_file: str,
    sample_rate: int
) -> Dict[str, Any]:
    """
    Create metadata summary for the transcription job.
    
    Args:
        transcriptions: List of successful transcriptions
        audio_file: Original audio file name
        sample_rate: Audio sample rate
        
    Returns:
        Dictionary containing transcription metadata
    """
    total_segments = len(transcriptions)
    total_duration = transcriptions[-1]['end_time'] if transcriptions else 0.0
    total_words = sum(len(t['text'].split()) for t in transcriptions)
    
    return {
        'source_audio': audio_file,
        'sample_rate': sample_rate,
        'total_segments': total_segments,
        'total_duration': total_duration,
        'total_words': total_words,
        'avg_segment_duration': total_duration / total_segments if total_segments > 0 else 0,
        'segments': transcriptions
    }
