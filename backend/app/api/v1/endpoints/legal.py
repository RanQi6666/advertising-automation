from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/legal")


@router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion_instructions() -> str:
    return """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Data Deletion Instructions</title>
    <style>
      body {
        color: #111827;
        font-family: Arial, Helvetica, sans-serif;
        line-height: 1.6;
        margin: 0;
        padding: 40px 20px;
        background: #f8fafc;
      }
      main {
        max-width: 760px;
        margin: 0 auto;
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 28px;
      }
      h1 {
        font-size: 26px;
        margin: 0 0 16px;
      }
      p {
        margin: 0 0 14px;
      }
      ul {
        margin: 0 0 14px 22px;
        padding: 0;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Data Deletion Instructions</h1>
      <p>
        This app is used to support advertising operations. If you want to
        request deletion of personal data associated with this app, please send
        a data deletion request to the app operator.
      </p>
      <p>Please include the following information in your request:</p>
      <ul>
        <li>Your Facebook user ID or the email address associated with your account.</li>
        <li>
          A short note stating that you are requesting deletion of data
          associated with this app.
        </li>
      </ul>
      <p>
        We will review and process valid deletion requests within a reasonable
        period of time after receiving the request.
      </p>
      <p>
        If you do not know where to send the request, please use the contact
        email configured for this app in Meta Developers.
      </p>
    </main>
  </body>
</html>
"""


@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy() -> str:
    return """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Privacy Policy</title>
    <style>
      body {
        color: #111827;
        font-family: Arial, Helvetica, sans-serif;
        line-height: 1.6;
        margin: 0;
        padding: 40px 20px;
        background: #f8fafc;
      }
      main {
        max-width: 760px;
        margin: 0 auto;
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 28px;
      }
      h1 {
        font-size: 26px;
        margin: 0 0 16px;
      }
      h2 {
        font-size: 18px;
        margin: 22px 0 10px;
      }
      p {
        margin: 0 0 14px;
      }
      ul {
        margin: 0 0 14px 22px;
        padding: 0;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Privacy Policy</h1>
      <p>
        This privacy policy explains how this app handles information when it is
        used to support advertising operations, campaign management, and related
        business workflows.
      </p>

      <h2>Information We Process</h2>
      <p>
        The app may process advertising campaign information, public page or ad
        account identifiers, creative materials, landing page URLs, and related
        operational data needed to create and manage ads.
      </p>

      <h2>How We Use Information</h2>
      <p>
        We use information only to operate advertising workflows, prepare ad
        payloads, manage campaign assets, and support authorized business users.
      </p>

      <h2>Sharing</h2>
      <p>
        We do not sell personal information. Information may be sent to Meta
        APIs only when an authorized user requests advertising or publishing
        actions.
      </p>

      <h2>Data Retention</h2>
      <p>
        We retain operational data only as long as needed for advertising
        workflow management, auditing, troubleshooting, or legal obligations.
      </p>

      <h2>Data Deletion</h2>
      <p>
        To request deletion of data associated with this app, please follow the
        data deletion instructions provided by the app operator or contact the
        email configured for this app in Meta Developers.
      </p>

      <h2>Contact</h2>
      <p>
        If you have questions about this privacy policy, please contact the
        email address configured for this app in Meta Developers.
      </p>
    </main>
  </body>
</html>
"""
