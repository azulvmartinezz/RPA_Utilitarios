"""Prueba mínima: verificar eventos Flet 0.85.3 con API correcta."""
import flet as ft


def main(page: ft.Page) -> None:
    page.title = "Test Flet Eventos"
    page.window_width = 500
    page.window_height = 400

    resultado = ft.Text("(sin seleccionar)", size=18, color="white")
    log = ft.Text("", size=12, color="#8ECAE6")

    # En Flet 0.85.3 Dropdown usa on_select (no on_change)
    def on_select(e):
        valor = e.data if hasattr(e, 'data') and e.data else (e.control.value if hasattr(e, 'control') else '?')
        print(f"[EVENT] on_select disparado: {valor} | e.data={getattr(e,'data',None)}")
        dd.value = valor
        resultado.value = f"Seleccionaste: {valor}"
        log.value = log.value + f"\n→ on_select: {valor}"
        page.update()

    def on_click(e):
        print("[EVENT] botón clickeado")
        log.value = log.value + "\n→ botón clickeado"
        page.update()

    dd = ft.Dropdown(
        value="Opción A",
        options=[
            ft.dropdown.Option("Opción A"),
            ft.dropdown.Option("Opción B"),
            ft.dropdown.Option("Opción C"),
        ],
        width=300,
    )
    dd.on_select = on_select  # Asignado después de crear el control

    btn = ft.ElevatedButton("Haz clic aquí", on_click=on_click)

    page.add(
        ft.Text("Prueba de eventos Flet 0.85.3", size=22, color="#FFB703"),
        dd,
        resultado,
        btn,
        log,
    )


if __name__ == "__main__":
    ft.run(main)
