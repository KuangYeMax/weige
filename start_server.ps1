$env:Path = "D:\weige\.venv\Scripts;" + $env:Path
Set-Location -LiteralPath "D:\weige"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
