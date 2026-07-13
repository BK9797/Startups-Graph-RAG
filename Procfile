web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
frontend: streamlit run app/frontend/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
