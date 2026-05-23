from datetime import datetime
import secrets

from flask import Blueprint, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from database.models import AdminInvite, AdminUser, db
from app import find_valid_invite, log_action, limiter


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session["is_admin"] = True
            session["admin_user"] = user.username
            session["admin_id"] = user.id
            session["admin_role"] = user.role or "admin"
            session.setdefault("csrf_token", secrets.token_urlsafe(32))
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

        if not username or not password or not email or not sec_code:
            error = "請填寫所有欄位"
        elif len(password) < 6:
            error = "密碼至少 6 碼"
        elif password != confirm:
            error = "密碼確認不一致"
        elif AdminUser.query.filter_by(username=username).first():
            error = "此帳號已存在"
        elif AdminUser.query.filter_by(email=email).first():
            error = "此 Email 已存在"
        else:
            invite = find_valid_invite(sec_code)
            if not invite:
                error = "安全碼無效或已使用，請向網站擁有者確認"
            else:
                role = invite.role or "admin"
                user = AdminUser(
                    username=username,
                    email=email,
                    password_hash=generate_password_hash(password),
                    role=role,
                )
                db.session.add(user)
                invite.used_at = datetime.utcnow()
                log_action("register_admin", target=username, details=f"role={role}")
                db.session.commit()

                session["is_admin"] = True
                session["admin_user"] = user.username
                session["admin_id"] = user.id
                session["admin_role"] = user.role or "admin"
                session.setdefault("csrf_token", secrets.token_urlsafe(32))
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
