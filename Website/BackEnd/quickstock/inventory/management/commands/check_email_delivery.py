import os

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError

from inventory.email_utils import email_delivery_status, get_delivery_connection


class Command(BaseCommand):
    help = "Validate production email configuration and send a real delivery probe."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            dest="recipient",
            default=os.getenv("QUICKSTOCK_EMAIL_TEST_RECIPIENT", ""),
            help="Recipient address for the delivery probe.",
        )

    def handle(self, *args, **options):
        recipient = str(options.get("recipient") or "").strip()
        if not recipient:
            raise CommandError("Provide --to or QUICKSTOCK_EMAIL_TEST_RECIPIENT.")

        status = email_delivery_status()
        if not status["ok"]:
            raise CommandError(status["detail"])

        message = EmailMessage(
            subject="QuickStock JA Render email check",
            body="Email delivery from the QuickStock JA online server is working.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=get_delivery_connection(),
        )
        sent_count = message.send(fail_silently=False)
        if sent_count != 1:
            raise CommandError("The email provider did not accept the delivery probe.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Email provider accepted the delivery probe for {recipient}."
            )
        )
