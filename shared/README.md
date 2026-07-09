# Shared

Esta carpeta es para la logica reutilizable entre la UI actual y la futura UI en Flet.

Primeras piezas a mover desde `ejecutable/app.py`:

- Calculo de rangos de fechas.
- Ejecucion de flujos seleccionados.
- Generacion de reporte consolidado.
- Autenticacion O365.
- Consolidacion final desde OneDrive.

Regla practica: aqui solo entra logica sin widgets, sin `messagebox`, sin `CTk*` y sin dependencias visuales.
