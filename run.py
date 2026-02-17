from gpon_turon import create_app


app = create_app()


if __name__ == "__main__":
    settings = app.config["SETTINGS"]
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
