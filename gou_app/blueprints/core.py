from sqlalchemy import text
from flask import Blueprint, Response, current_app, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..forms import EmptyForm, RoundForm
from ..models import ChitGroup, ChitCycle, GroupMembership, Member, User
from ..services import build_dashboard_metrics

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
@login_required
def dashboard():
    if current_user.role == "Customer":
        member = Member.query.filter_by(id=current_user.member_id, deleted=False).first() if current_user.member_id else None
        recent_payments = []
        if member:
            recent_payments = sorted(
                [payment for payment in member.payments if not payment.deleted],
                key=lambda item: item.timestamp or item.created_at,
                reverse=True,
            )[:8]
        return render_template("customer_dashboard.html", member=member, recent_payments=recent_payments)

    search = (request.args.get("q") or "").strip().lower()
    members = []
    groups = []
    pending_users = []
    active_cycles = []
    action_form = EmptyForm()
    round_forms = {}
    metrics = {
        "total_collections": 0.0,
        "total_penalties": 0.0,
        "pending_payments": 0,
        "overdue_memberships": [],
        "profit": 0.0,
        "active_groups": 0,
        "closed_auctions": 0,
    }

    try:
        members = Member.query.filter_by(deleted=False).order_by(Member.name).all()
        if search:
            members = [
                member
                for member in members
                if search in member.name.lower()
                or search in (member.phone or "").lower()
                or search in (member.email or "").lower()
            ]
    except Exception:
        current_app.logger.exception("Dashboard member query failed")

    try:
        groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
        round_forms = {group.id: RoundForm(next_round=str(min(group.current_round + 1, group.total_members))) for group in groups}
    except Exception:
        current_app.logger.exception("Dashboard group query failed")

    try:
        if current_user.role == "Admin":
            pending_users = User.query.filter_by(is_approved=False, deleted=False).order_by(User.created_at.asc()).all()
    except Exception:
        current_app.logger.exception("Dashboard pending users query failed")

    try:
        metrics = build_dashboard_metrics()
    except Exception:
        current_app.logger.exception("Dashboard metrics build failed")

    try:
        active_cycles = ChitCycle.query.filter_by(deleted=False).order_by(ChitCycle.auction_date.asc()).all()
    except Exception:
        current_app.logger.exception("Dashboard cycles query failed")

    return render_template(
        "dashboard.html",
        members=members,
        groups=groups,
        pending_users=pending_users,
        action_form=action_form,
        round_forms=round_forms,
        metrics=metrics,
        active_cycles=active_cycles,
        search=search,
    )


@core_bp.route("/health")
def health():
    db.session.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "db": "connected"})


@core_bp.route("/manifest.webmanifest")
def manifest():
    return jsonify(
        {
            "name": "Balaji Chit Funds",
            "short_name": "Balaji Chit",
            "description": "Chit fund member, payment, auction, and report management.",
            "start_url": url_for("auth.login"),
            "scope": "/",
            "display": "standalone",
            "background_color": "#0d1017",
            "theme_color": "#14b8a6",
            "icons": [
                {
                    "src": url_for("core.pwa_icon"),
                    "sizes": "512x512",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        }
    )


@core_bp.route("/sw.js")
def service_worker():
    script = """
const CACHE_NAME = "balaji-chit-pwa-v1";
const URLS = ["/login", "/register", "/health"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/login")))
  );
});
""".strip()
    return Response(script, mimetype="application/javascript")


@core_bp.route("/pwa-icon.svg")
def pwa_icon():
    svg = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#14b8a6"/>
      <stop offset="100%" stop-color="#0f766e"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="120" fill="#0d1017"/>
  <rect x="36" y="36" width="440" height="440" rx="96" fill="url(#bg)" opacity="0.18"/>
  <path d="M256 90 382 164v184L256 422 130 348V164z" fill="none" stroke="#d8fffb" stroke-width="28" stroke-linejoin="round"/>
  <circle cx="256" cy="256" r="48" fill="#d8fffb"/>
  <text x="256" y="466" text-anchor="middle" font-family="Arial, sans-serif" font-size="42" font-weight="700" fill="#d8fffb">BCF</text>
</svg>
""".strip()
    return Response(svg, mimetype="image/svg+xml")
