$python = "C:\Users\Luca\AppData\Local\Programs\Python\Python314\python.exe"
$backend = "D:\BIVIO\backend"

Start-Process -FilePath $python -ArgumentList "-m uvicorn main:app --port 8000" -WorkingDirectory $backend -WindowStyle Hidden
Start-Sleep 2
Start-Process -FilePath "cloudflared" -ArgumentList "tunnel run bivio" -WindowStyle Hidden
