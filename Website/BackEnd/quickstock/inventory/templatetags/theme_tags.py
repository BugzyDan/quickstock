from django import template
from django.utils.html import format_html, format_html_join


register = template.Library()

VALID_THEMES = {"system", "light", "dark"}


def _resolve_theme(user):
    if not getattr(user, "is_authenticated", False):
        return "light"

    profile = getattr(user, "profile", None)
    theme = getattr(profile, "theme", None) or "system"
    return theme if theme in VALID_THEMES else "system"


def _resolve_theme_from_context(context, user=None):
    request = context.get("request")
    resolved_user = user
    if request is not None and (
        resolved_user is None or not getattr(resolved_user, "is_authenticated", False)
    ):
        resolved_user = getattr(request, "user", resolved_user)
    return _resolve_theme(resolved_user)


def _theme_attributes(theme, *, body=False):
    attrs = [format_html('data-theme="{}"', theme)]
    if theme != "system":
        attrs.append(format_html('data-theme-applied="{}"', theme))
        if not body and theme == "dark":
            attrs.append(format_html('class="force-dark"'))
    return format_html_join(" ", "{}", ((attr,) for attr in attrs))


@register.simple_tag(takes_context=True)
def html_theme_attrs(context, user=None):
    theme = _resolve_theme_from_context(context, user)
    return _theme_attributes(theme)


@register.simple_tag(takes_context=True)
def body_theme_attrs(context, user=None):
    theme = _resolve_theme_from_context(context, user)
    return _theme_attributes(theme, body=True)
