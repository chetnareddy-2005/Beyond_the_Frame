$ErrorActionPreference = "Stop"

$env:BTF_SMTP_HOST = "smtp.gmail.com"
$env:BTF_SMTP_PORT = "587"
$env:BTF_SMTP_USERNAME = "chetnareddy2520@gmail.com"
$env:BTF_SMTP_FROM = "Beyond the Frame <chetnareddy2520@gmail.com>"

$secret = Read-Host "Paste Gmail App Password" -AsSecureString
$env:BTF_SMTP_PASSWORD = ([Net.NetworkCredential]::new("", $secret)).Password.Replace(" ", "")

python -m backend.server
