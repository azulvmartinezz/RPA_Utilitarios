# shared

Código común de los jobs. No se corre solo.

| Módulo | Para qué | Lo usan |
|---|---|---|
| `db.py` | Conexión a Postgres desde el secreto del ambiente (con keepalives) | Todos los que tocan la base |
| `credenciales.py` | Descifra las credenciales de proveedores de la base | Supramax (saldos, dispersión, consumos) |
| `supramax_login.py` | Login del portal de Supramax con Selenium | Saldos y dispersión |
| `pool_navegadores.py`, `navegador.py` | Pool de Chrome headless y localización del binario | Supramax, Libro Azul, Pase |
| `diagnostico.py` | Guarda HTML + PNG cuando un portal falla | Supramax |
| `numeros.py` | Lectura de importes del portal | Supramax, Libro Azul |
