# ============================================================
# PATCH: main.py — endpoint /api/shutdown mejorado
# ============================================================
# Reemplaza el @app.route("/api/shutdown") existente.
#
# El problema del original: _shutdown.set() hace que el watchdog
# salga, pero como Flask corre en un hilo daemon, si el proceso
# recibe SIGTERM antes de que Flask procese la respuesta, el
# cliente nunca recibe el 200 OK y cree que el backend ya murió.
#
# La solución: responder primero, luego apagar en hilo separado
# con un delay mínimo para que la respuesta llegue al cliente.
# ============================================================

@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """
    Apaga el backend de forma controlada.
    
    Flujo:
      1. Responde 200 OK inmediatamente (el cliente recibe confirmación)
      2. Espera 300ms en hilo separado (tiempo para que la respuesta viaje)
      3. Setea _shutdown → el watchdog sale → finally corre limpiamente
         (libera cámara, borra PID file, cierra conexiones)
    """
    logger.info("Shutdown solicitado vía API — respondiendo antes de apagar")

    def _delayed_shutdown():
        time.sleep(0.3)  # 300ms: suficiente para que el 200 OK llegue al cliente
        logger.info("Ejecutando shutdown ordenado...")
        _shutdown.set()
        # Dar 2 segundos al watchdog para que salga limpiamente
        # antes de forzar la salida del proceso
        time.sleep(2.0)
        logger.info("Proceso terminando.")
        os._exit(0)  # Salida limpia que no levanta excepciones en hilos daemon

    t = threading.Thread(target=_delayed_shutdown, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Apagando backend..."})


# ============================================================
# TAMBIÉN: asegúrate de que _watchdog() responde a _shutdown
# correctamente. El tuyo ya lo hace — solo verifica que tenga
# esto en su loop:
#
#   while not _shutdown.is_set():
#       ...
#       _shutdown.wait(timeout=5.0)  # ← esto ya está en tu código
#
# Y que el finally de __main__ limpie todo:
#
#   finally:
#       _shutdown.set()
#       _capture_running.clear()
#       _source.close()
#       pid_file.unlink(missing_ok=True)   # ← esto ya está
#       logger.info("Sistema detenido")
#
# No necesitas cambiar nada más en main.py.
# ============================================================
