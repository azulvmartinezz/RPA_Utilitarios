# Migracion a Flet sin romper la app estable

## Estado

- `ejecutable/app.py`: version estable y presentable.
- `ui_flet/`: nueva interfaz en construccion.
- `shared/`: logica compartida para evitar duplicaciones.

## Orden recomendado

1. Mover calculo de fechas a `shared/`. `Hecho`
2. Mover ejecucion de flujos a un servicio compartido. `Hecho`
3. Mover auth O365 a un servicio compartido. `Hecho`
4. Mover consolidacion final a un servicio compartido. `Hecho`
5. Conectar Flet a esos servicios. `En progreso`
6. Probar Flet de punta a punta.
7. Solo entonces retirar o archivar la UI anterior.

## Que sacar primero de `ejecutable/app.py`

- `calcular_fechas()`
- `actualizar_info_fechas()`
- `run_pipeline()`
- `generar_reporte_consolidado()`
- `run_auth()`
- `run_consolidation()`

## Que debe quedarse en cada UI

Tk/CustomTkinter:

- Ventanas.
- Calendario.
- Message boxes.
- Selector de archivos.
- Estado visual de botones.

Flet:

- Layout.
- Navegacion.
- Estados visuales.
- Panel de logs.
- Formularios y acciones del usuario.

## Criterio de reemplazo

Flet reemplaza a `app.py` solo cuando ya cubra:

- Seleccion de flujos.
- Rangos de fecha.
- Ejecucion completa.
- Logs visibles.
- Auth O365.
- Consolidacion final.
- Empaquetado aceptable para el usuario final.
