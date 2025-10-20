#! /bin/bash

if [ $# != 1 ]; then
  echo "Incorrect number of arguments. Please provide exactly one argument."
  exit 1
fi

echo "Granting cluster admin roe to user $1"

rosa grant user cluster-admin --user=$1 --cluster=2luh6lh57rods08un62hhtq7ggdihhls
