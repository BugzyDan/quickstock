from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

def send_verification_email(user_email, username, code):
    subject = "Verify your QuickStock Account"
    from_email = settings.DEFAULT_FROM_EMAIL
    
    # You can create a template at templates/emails/verify.html
    html_content = render_to_string('emails/verify.html', {
        'username': username,
        'code': code
    })
    
    msg = EmailMultiAlternatives(subject, "", from_email, [user_email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()