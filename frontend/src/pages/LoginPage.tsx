import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, signup, forgotPassword } from "../api/client";
import { useAuth } from "../state/AuthContext";
import { Map } from "lucide-react";

const inputCls =
  "w-full border border-slate-200 rounded-md px-3 py-2.5 text-sm text-slate-900 bg-white focus:outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 placeholder:text-slate-400";
const labelCls = "block text-xs font-medium text-slate-700 mb-1.5";

function Field({ id, label, type = "text", value, onChange, placeholder, required = true }: {
  id: string; label: string; type?: string; value: string; onChange: (v: string) => void; placeholder: string; required?: boolean;
}) {
  return (
    <div>
      <label htmlFor={id} className={labelCls}>{label}</label>
      <input id={id} type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} required={required} className={inputCls} />
    </div>
  );
}

export function LoginPage() {
  const { loginWithSession } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState<"signin" | "signup" | "forgot">("signin");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Empty by default — the backend resolves the user uniquely by email
  // when no slug is given. SUPERADMINs (platform tenant) and customer
  // tenants (their own slug) all log in cleanly without typing it.
  // Filling it in only matters for the rare case of duplicate emails
  // across tenants, which the backend handles by requiring a slug then.
  const [tenantSlug, setTenantSlug] = useState("");

  const [companyName, setCompanyName] = useState("");
  const [fullName, setFullName] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotSent, setForgotSent] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSignin(e: React.FormEvent) {
    e.preventDefault(); setError(null); setLoading(true);
    try {
      const res = await login(email.trim(), password, tenantSlug.trim());
      loginWithSession(res.access_token, res.refresh_token);
      navigate("/", { replace: true });
    } catch (err) { setError(err instanceof Error ? err.message : "Login failed"); }
    finally { setLoading(false); }
  }

  async function handleSignup(e: React.FormEvent) {
    e.preventDefault();
    if (signupPassword !== confirmPassword) { setError("Passwords do not match"); return; }
    setError(null); setLoading(true);
    try {
      const res = await signup({ company_name: companyName.trim(), full_name: fullName.trim(), email: signupEmail.trim(), password: signupPassword });
      loginWithSession(res.access_token, res.refresh_token);
      navigate("/", { replace: true });
    } catch (err) { setError(err instanceof Error ? err.message : "Account creation failed"); }
    finally { setLoading(false); }
  }

  async function handleForgot(e: React.FormEvent) {
    e.preventDefault(); setError(null); setLoading(true);
    try { await forgotPassword(forgotEmail.trim()); setForgotSent(true); }
    catch (err) { setError(err instanceof Error ? err.message : "Request failed"); }
    finally { setLoading(false); }
  }

  function switchTab(t: "signin" | "signup" | "forgot") { setTab(t); setError(null); setForgotSent(false); }

  return (
    <div className="min-h-screen flex">
      {/* Left — dark panel */}
      <div className="hidden lg:flex lg:w-[420px] xl:w-[480px] shrink-0 flex-col justify-between p-10 bg-slate-900">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded bg-accent-500 flex items-center justify-center">
            <Map size={15} className="text-white" />
          </div>
          <span className="text-sm font-semibold text-white tracking-tight">FleetFlow</span>
        </div>

        <div>
          <p className="text-2xl font-semibold text-white leading-snug">
            Last-mile delivery, built for scale.
          </p>
          <p className="mt-3 text-sm text-slate-400 leading-relaxed">
            Route optimization, live courier tracking, SLA management, and full delivery analytics — all in one place.
          </p>

          <div className="mt-8 space-y-3">
            {[
              { label: "Real-time map",     desc: "Live courier positions, route progress, exception flags" },
              { label: "Smart routing",      desc: "Optimization runs with full explainability and manual override" },
              { label: "Multi-tenant",       desc: "Each workspace is fully isolated — data, settings, users" },
            ].map(({ label, desc }) => (
              <div key={label} className="flex items-start gap-3">
                <div className="mt-1 w-1.5 h-1.5 rounded-full bg-accent-500 shrink-0" />
                <div>
                  <p className="text-xs font-medium text-slate-200">{label}</p>
                  <p className="text-xs text-slate-500 mt-0.5">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="text-[10px] text-slate-600">© {new Date().getFullYear()} FleetFlow</p>
      </div>

      {/* Right — form panel */}
      <div className="flex-1 flex items-center justify-center bg-white px-6 py-12">
        <div className="w-full max-w-sm">
          {/* Mobile logo */}
          <div className="flex items-center gap-2 mb-8 lg:hidden">
            <div className="w-7 h-7 rounded bg-accent-500 flex items-center justify-center">
              <Map size={13} className="text-white" />
            </div>
            <span className="text-sm font-semibold text-slate-900">FleetFlow</span>
          </div>

          {tab !== "forgot" && (
            <>
              <div className="mb-6">
                <h2 className="text-lg font-semibold text-slate-900">
                  {tab === "signin" ? "Sign in" : "Create account"}
                </h2>
                <p className="text-xs text-slate-500 mt-1">
                  {tab === "signin" ? "Access your workspace" : "Set up a new workspace"}
                </p>
              </div>

              <div className="flex border-b border-slate-200 mb-6">
                {(["signin", "signup"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => switchTab(t)}
                    className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors duration-150 ${
                      tab === t
                        ? "border-accent-500 text-slate-900"
                        : "border-transparent text-slate-400 hover:text-slate-700"
                    }`}
                  >
                    {t === "signin" ? "Sign in" : "Create account"}
                  </button>
                ))}
              </div>
            </>
          )}

          {error && (
            <div data-testid="auth-error" className="mb-4 border-l-2 border-l-red-500 pl-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          {/* Sign in form */}
          {tab === "signin" && (
            <form onSubmit={(e) => void handleSignin(e)} className="space-y-4">
              <Field id="signin-email"     label="Email"           type="email"    value={email}       onChange={setEmail}       placeholder="you@company.com" />
              <Field id="signin-password"  label="Password"        type="password" value={password}    onChange={setPassword}    placeholder="Enter password" />
              <Field id="signin-workspace" label="Workspace slug (optional)"  type="text"     value={tenantSlug}  onChange={setTenantSlug}  placeholder="leave blank for most users" required={false} />
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-md bg-accent-500 text-white text-sm font-medium py-2.5 hover:bg-accent-600 disabled:opacity-50 transition-colors duration-150 mt-1"
              >
                {loading ? "Signing in…" : "Sign in"}
              </button>
              <p className="text-xs text-center text-slate-400">
                <button type="button" onClick={() => switchTab("forgot")} className="text-slate-500 hover:text-slate-700 underline">
                  Forgot password?
                </button>
              </p>
            </form>
          )}

          {/* Sign up form */}
          {tab === "signup" && (
            <form onSubmit={(e) => void handleSignup(e)} className="space-y-4">
              <Field id="signup-company"   label="Company name"    value={companyName}     onChange={setCompanyName}     placeholder="Acme Logistics" />
              <Field id="signup-fullname"  label="Full name"       value={fullName}        onChange={setFullName}        placeholder="Alex Smith" />
              <Field id="signup-email"     label="Work email"      type="email"            value={signupEmail}          onChange={setSignupEmail}     placeholder="alex@acme.com" />
              <div className="grid grid-cols-2 gap-3">
                <Field id="signup-pw"      label="Password"        type="password"         value={signupPassword}       onChange={setSignupPassword}  placeholder="8+ characters" />
                <Field id="signup-confirm" label="Confirm"         type="password"         value={confirmPassword}      onChange={setConfirmPassword} placeholder="Repeat" />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-md bg-accent-500 text-white text-sm font-medium py-2.5 hover:bg-accent-600 disabled:opacity-50 transition-colors duration-150"
              >
                {loading ? "Creating…" : "Create account"}
              </button>
            </form>
          )}

          {/* Forgot password */}
          {tab === "forgot" && (
            <div className="space-y-5">
              <div>
                <button type="button" onClick={() => switchTab("signin")} className="text-xs text-slate-400 hover:text-slate-700 mb-4 block">← Back to sign in</button>
                <h2 className="text-lg font-semibold text-slate-900">Reset password</h2>
                <p className="text-xs text-slate-500 mt-1">Enter your email and we'll send a reset link.</p>
              </div>
              {forgotSent ? (
                <div className="border-l-2 border-l-emerald-500 pl-3 py-2 text-sm text-emerald-700">
                  If that email exists, a reset link has been sent.
                </div>
              ) : (
                <form onSubmit={(e) => void handleForgot(e)} className="space-y-4">
                  <Field id="forgot-email" label="Email" type="email" value={forgotEmail} onChange={setForgotEmail} placeholder="you@company.com" />
                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full rounded-md bg-accent-500 text-white text-sm font-medium py-2.5 hover:bg-accent-600 disabled:opacity-50 transition-colors duration-150"
                  >
                    {loading ? "Sending…" : "Send reset link"}
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
