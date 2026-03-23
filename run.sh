#!/bin/bash
# Run the Lockout pipeline locally.
# Requires: pip install -r requirements.txt
#
# Set TAP_ENDPOINT_URL if your local tap is running:
#   export TAP_ENDPOINT_URL=http://localhost:PORT/feed
# Leave unset to use the mock feed.

streamlit run app.py
