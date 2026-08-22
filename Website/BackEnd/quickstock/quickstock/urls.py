from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic.base import RedirectView
from inventory.views import logout_view, change_password_view
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.conf.urls.static import static
from django.views.static import serve as serve_media
from inventory.health import readiness_view

urlpatterns = [
    path('healthz/', readiness_view, name='render_readiness'),
    path('admin/', admin.site.urls),

    # Include all inventory app URLs (dashboard, sales, inventory, etc.)
    path('', include('inventory.urls')),

    # Include the REST API endpoints
    path('api/', include('api.urls')),

    # Standard logout redirect
    path('accounts/logout/', RedirectView.as_view(pattern_name='logout', permanent=False)),

    # Password management (project-wide shortcut)
    path('change-password/', change_password_view, name='change_password'),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif getattr(settings, "MEDIA_STORAGE_PROVIDER", "filesystem") == "filesystem":
    # Receipt logos are public business branding. Object storage is preferred,
    # but this keeps them reachable on hosts using local media storage.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_media, {"document_root": settings.MEDIA_ROOT}),
    ]
