@echo off
cd /d %~dp0

REM --- Identifiants admin (modifie le mot de passe ici si tu veux) ---
set DJIBSHOP_ADMIN_USER=Djibson224
set DJIBSHOP_ADMIN_PASSWORD=succes2025!
set DJIBSHOP_SECRET_KEY=djibshop_cle_secrete_fixe_pour_les_sessions_64car

echo Demarrage de DjibShop...
echo.
python .\Backend\server.py

echo.
echo Le serveur s'est arrete ou une erreur est survenue (voir ci-dessus).
pause
