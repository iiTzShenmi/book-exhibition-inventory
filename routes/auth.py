from datetime import datetime
import secrets

from flask import Blueprint, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from database.models import AdminUser, db
from app import claim_invite, find_valid_invite, log_action, limiter


auth_bp = Blueprint("auth", __name__)
REGISTRATION_FAILURE_MESSAGE = "註冊資訊無法驗證，請確認資料與邀請碼後再試。"


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["is_admin"] = True
            session["admin_user"] = user.username
            session["admin_id"] = user.id
            session["admin_role"] = user.role or "admin"
            session["csrf_token"] = secrets.token_urlsafe(32)
            session.permanent = True
            try:
                log_action("login_success", target=user.username)
                db.session.commit()
            except Exception:
                db.session.rollback()
            return redirect(url_for("admin.dashboard"))
        else:
            try:
                log_action("login_failed", target=username or "(blank)", details="invalid_credentials")
                db.session.commit()
            except Exception:
                db.session.rollback()
            error = "Invalid username or password"
    return render_template("login.html", title="Admin Login", error=error)


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register():
    if session.get("is_admin"):
        return redirect(url_for("admin.dashboard"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        sec_code = (request.form.get("security_code") or "").strip()

        invalid_input = (
            not username
            or not password
            or not email
            or not sec_code
            or len(password) < 6
            or password != confirm
        )
        existing_user = (
            AdminUser.query.filter_by(username=username).first()
            if username
            else None
        )
        existing_email = (
            AdminUser.query.filter_by(email=email).first()
            if email
            else None
        )
        invite = find_valid_invite(sec_code) if not invalid_input else None

        if invalid_input or existing_user or existing_email or not invite:
            error = REGISTRATION_FAILURE_MESSAGE
        else:
            role = invite.role or "admin"
            user = AdminUser(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                role=role,
            )
            try:
                db.session.add(user)
                if not claim_invite(invite.id, datetime.utcnow()):
                    db.session.rollback()
                    error = REGISTRATION_FAILURE_MESSAGE
                else:
                    log_action("register_admin", target=username, details=f"role={role}")
                    db.session.commit()
            except Exception:
                db.session.rollback()
                error = REGISTRATION_FAILURE_MESSAGE

            if error is None:
                session.clear()
                session["is_admin"] = True
                session["admin_user"] = user.username
                session["admin_id"] = user.id
                session["admin_role"] = user.role or "admin"
                session["csrf_token"] = secrets.token_urlsafe(32)
                session.permanent = True
                try:
                    log_action("login_success", target=user.username, details="auto after register")
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                return redirect(url_for("admin.dashboard"))

    return render_template("register.html", title="Admin Register", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    actor = session.get("admin_user") or "unknown"
    if session.get("is_admin"):
        try:
            log_action("logout", target=actor)
            db.session.commit()
        except Exception:
            db.session.rollback()
    session.clear()
    return redirect(url_for("inventory.home"))
