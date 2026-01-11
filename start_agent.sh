#!/bin/bash
cd /Users/aidanchen/projects/nomad
source venv/bin/activate

# Fix SSL certificate verification for Python 3.11 on macOS
export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")
export REQUESTS_CA_BUNDLE=$SSL_CERT_FILE

echo "SSL_CERT_FILE=$SSL_CERT_FILE"
python agent.py dev
