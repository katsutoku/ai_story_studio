from flask import Blueprint, render_template

from ..models import Project

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def dashboard():
    """ダッシュボード：作品一覧を表示"""
    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return render_template("dashboard.html", projects=projects)
