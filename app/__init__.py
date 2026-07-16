from pathlib import Path

from flask import Flask, render_template

from config import Config
from .extensions import db, csrf


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    # instance / データ保存用ディレクトリを用意
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["CHAPTER_MD_DIR"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)

    from .routes.main import main_bp
    from .routes.projects import projects_bp
    from .routes.characters import characters_bp
    from .routes.world_settings import world_settings_bp
    from .routes.chapters import chapters_bp
    from .routes.foreshadowings import foreshadowings_bp
    from .routes.plot import plot_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(characters_bp)
    app.register_blueprint(world_settings_bp)
    app.register_blueprint(chapters_bp)
    app.register_blueprint(foreshadowings_bp)
    app.register_blueprint(plot_bp)

    register_error_handlers(app)

    with app.app_context():
        db.create_all()

    return app


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        # 存在しないデータへアクセスした場合は404エラー画面を表示する
        return render_template("404.html"), 404
