"""FastAPI application entrypoint for ExpenseFlow."""

from dotenv import load_dotenv
from fastapi import FastAPI

from app.db import init_db
from app.routes import router

load_dotenv()

app = FastAPI(title="ExpenseFlow")
app.include_router(router)

init_db()
