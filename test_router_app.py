"""
test_router_app.py
──────────────────
Minimal FastAPI app to test docx_pipeline_router in isolation.
Run:  python test_router_app.py
Then open: http://localhost:8001/docs
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from docx_pipeline_router import router as docx_router

app = FastAPI(title="DOCX Pipeline Router — Test")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the router — your existing backend would do the same
app.include_router(docx_router, prefix="/docx", tags=["DOCX Pipeline"])

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("test_router_app:app", host="0.0.0.0", port=8001, reload=True)
