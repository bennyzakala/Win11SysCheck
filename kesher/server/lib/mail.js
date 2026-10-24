// Outgoing email. With RESEND_API_KEY set, mail goes out through Resend (resend.com);
// without it there is no mailer, and the app skips address verification.

export function resendMailer({ apiKey, from }) {
  return {
    async send({ to, subject, html, text }) {
      const r = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { authorization: `Bearer ${apiKey}`, 'content-type': 'application/json' },
        body: JSON.stringify({ from, to: [to], subject, html, text })
      });
      if (!r.ok) throw new Error(`Email failed: ${r.status} ${await r.text()}`);
    }
  };
}

export function mailerFromEnv(get) {
  const apiKey = get('RESEND_API_KEY');
  return apiKey ? resendMailer({ apiKey, from: get('MAIL_FROM') || 'Kesher <onboarding@resend.dev>' }) : null;
}

// A short Hebrew email with one button
export function linkEmail({ title, intro, button, url, outro }) {
  const html = `<div dir="rtl" style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;color:#17151C">
    <h1 style="font-size:22px;margin:0 0 12px">${title}</h1><p style="font-size:16px;line-height:1.5">${intro}</p>
    <p style="margin:24px 0"><a href="${url}" style="background:#17151C;color:#fff;padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:bold">${button}</a></p>
    <p style="font-size:13px;color:#6F6A7A;line-height:1.5">${outro}<br><span dir="ltr">${url}</span></p></div>`;
  return { html, text: `${title}\n\n${intro}\n\n${url}\n\n${outro}` };
}
