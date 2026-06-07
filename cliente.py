import argparse
import json
import socket


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001

sesion = {"usuario": None, "contrasena": None}
config = {"host": DEFAULT_HOST, "port": DEFAULT_PORT}


def enviar_solicitud(payload):
    try:
        with socket.create_connection((config["host"], config["port"]), timeout=5) as sock:
            file = sock.makefile("rwb")
            file.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            file.flush()

            raw_response = file.readline()
            if not raw_response:
                return {"ok": False, "error": "El servidor cerro la conexion."}

            return json.loads(raw_response.decode("utf-8"))
    except ConnectionRefusedError:
        return {"ok": False, "error": "No se puede conectar al servidor."}
    except socket.timeout:
        return {"ok": False, "error": "La conexion con el servidor expiro."}
    except OSError as exc:
        return {"ok": False, "error": f"Error de red: {exc}"}
    except json.JSONDecodeError:
        return {"ok": False, "error": "El servidor respondio con JSON invalido."}


def credenciales():
    return {"usuario": sesion["usuario"], "contrasena": sesion["contrasena"]}


def mostrar_respuesta(respuesta):
    if respuesta.get("ok"):
        print(f"  {respuesta.get('mensaje', 'Operacion realizada.')}")
    else:
        print(f"  Error: {respuesta.get('error', 'Error desconocido.')}")


def registrar():
    usuario = input("  Nombre de usuario: ").strip()
    contrasena = input("  Contrasena: ").strip()
    respuesta = enviar_solicitud(
        {"accion": "registrar", "usuario": usuario, "contrasena": contrasena}
    )
    mostrar_respuesta(respuesta)


def login():
    usuario = input("  Usuario: ").strip()
    contrasena = input("  Contrasena: ").strip()
    respuesta = enviar_solicitud(
        {"accion": "login", "usuario": usuario, "contrasena": contrasena}
    )

    if respuesta.get("ok"):
        sesion["usuario"] = usuario
        sesion["contrasena"] = contrasena

    mostrar_respuesta(respuesta)


def ver_tareas():
    respuesta = enviar_solicitud({"accion": "listar_tareas", **credenciales()})
    if not respuesta.get("ok"):
        mostrar_respuesta(respuesta)
        return

    tareas = respuesta.get("tareas", [])
    if not tareas:
        print("  No tenes tareas registradas.")
        return

    print(f"  Tareas de {sesion['usuario']}:")
    for tarea in tareas:
        print(f"    #{tarea['id']} - {tarea['descripcion']} ({tarea['creada_en']})")


def crear_tarea():
    descripcion = input("  Descripcion de la tarea: ").strip()
    if not descripcion:
        print("  La descripcion no puede estar vacia.")
        return

    respuesta = enviar_solicitud(
        {"accion": "crear_tarea", "descripcion": descripcion, **credenciales()}
    )

    if respuesta.get("ok"):
        tarea = respuesta["tarea"]
        print(f"  Tarea creada con id #{tarea['id']}.")
    else:
        mostrar_respuesta(respuesta)


def eliminar_tarea():
    ver_tareas()
    try:
        tarea_id = int(input("  ID de la tarea a eliminar: ").strip())
    except ValueError:
        print("  ID invalido.")
        return

    respuesta = enviar_solicitud(
        {"accion": "eliminar_tarea", "id": tarea_id, **credenciales()}
    )
    mostrar_respuesta(respuesta)


def cerrar_sesion():
    sesion["usuario"] = None
    sesion["contrasena"] = None
    print("  Sesion cerrada.")


MENU = [
    ("Registrar usuario", registrar),
    ("Iniciar sesion", login),
    ("Ver mis tareas", ver_tareas),
    ("Crear tarea", crear_tarea),
    ("Eliminar tarea", eliminar_tarea),
    ("Cerrar sesion", cerrar_sesion),
    ("Salir", None),
]


def requiere_sesion(accion):
    return accion in (ver_tareas, crear_tarea, eliminar_tarea, cerrar_sesion)


def main():
    print("=== Sistema distribuido de gestion de tareas ===")
    print(f"Servidor: {config['host']}:{config['port']}")

    while True:
        logueado = (
            f"(logueado como {sesion['usuario']})"
            if sesion["usuario"]
            else "(sin sesion)"
        )
        print(f"\n{logueado}")
        for i, (nombre, _) in enumerate(MENU, 1):
            print(f"  {i}. {nombre}")

        opcion = input("Elegi una opcion: ").strip()
        if not opcion.isdigit() or not (1 <= int(opcion) <= len(MENU)):
            print("  Opcion invalida.")
            continue

        idx = int(opcion) - 1
        nombre, accion = MENU[idx]

        if accion is None:
            print("  Hasta luego!")
            break

        if requiere_sesion(accion) and not sesion["usuario"]:
            print("  Necesitas iniciar sesion primero.")
            continue

        print(f"\n--- {nombre} ---")
        accion()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Cliente TCP para gestion distribuida de tareas."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config["host"] = args.host
    config["port"] = args.port
    main()
