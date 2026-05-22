// hCaptcha integration. When HCAPTCHA_SECRET is unset, this is a pass-through
// — feedback launches without CAPTCHA. To enable, set HCAPTCHA_SECRET and
// NEXT_PUBLIC_HCAPTCHA_SITE_KEY, mount the widget client-side, and submit the
// token. No code change needed beyond the form widget.
export async function verifyCaptcha(token: string | undefined): Promise<boolean> {
  const secret = process.env.HCAPTCHA_SECRET;
  if (!secret) return true;
  if (!token) return false;
  try {
    const res = await fetch("https://hcaptcha.com/siteverify", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ secret, response: token }).toString(),
    });
    if (!res.ok) {
      throw new Error(`hCaptcha siteverify HTTP ${res.status}`);
    }
    const data = (await res.json()) as { success?: boolean };
    return Boolean(data.success);
  } catch (e) {
    console.error("[captcha] siteverify failed", e);
    return false;
  }
}
