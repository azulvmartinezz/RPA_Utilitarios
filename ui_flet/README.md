# UI Flet

Esta carpeta contiene la nueva interfaz en desarrollo.

Objetivo:

- Construir una UI mas amigable sin tocar la app estable en `ejecutable/app.py`.
- Probar la nueva experiencia en paralelo.
- Reutilizar logica compartida desde `shared/`.

Estado actual:

- Ya consume logica compartida para fechas.
- Ya dispara pipeline, auth O365 y consolidacion contra servicios reales en `shared/`.
- Sigue siendo experimental; la version estable para presentar sigue siendo `ejecutable/app.py`.

Regla de trabajo:

- No mover la app estable hasta que Flet cubra los flujos criticos con estabilidad.
- Toda nueva logica reusable debe vivir en `shared/`, no duplicada entre UIs.

Arranque local:

```bash
pip install flet
python ui_flet/main.py
```
