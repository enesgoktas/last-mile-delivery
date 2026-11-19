import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { acceptInvite } from "../api/client";
import { saveAuthTokens } from "../state/authStorage";

const PASSWORD_HINT = "Min 8 characters, 1 uppercase, 1 digit, 1 special character";

export function AcceptInvitePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      const tokens = await acceptInvite(token, password);
      saveAuthTokens({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
      setDone(true);
      setTimeout(() => navigate("/"), 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to activate account.");
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
        <div className="bg-white rounded-2xl border border-slate-200 p-8 max-w-md w-full text-center shadow-lg">
          <p className="text-red-600 font-medium mb-2">Invalid invitation link</p>
          <p className="text-sm text-slate-500 mb-6">This link is missing a token. Please ask your workspace admin to resend the invitation.</p>
          <button
            onClick={() => navigate("/login")}
            className="px-4 py-2 text-sm font-medium text-white bg-slate-900 rounded-xl hover:bg-slate-800"
          >
            Back to Sign In
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <div className="bg-white rounded-2xl border border-slate-200 p-8 max-w-md w-full shadow-lg">
        <h1 className="text-xl font-semibold text-slate-900 mb-1">Welcome — set your password</h1>
        <p className="text-sm text-slate-500 mb-6">Choose a strong password to activate your account.</p>

        {done ? (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            Account activated! Redirecting to the dashboard…
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">Password</label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={PASSWORD_HINT}
                className="w-full rounded-2xl border border-slate-200 bg-white px-3.5 py-3 text-sm text-slate-900 outline-none transition-colors focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
              />
              <p className="mt-1 text-xs text-slate-400">{PASSWORD_HINT}</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">Confirm password</label>
              <input
                type="password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="Repeat password"
                className="w-full rounded-2xl border border-slate-200 bg-white px-3.5 py-3 text-sm text-slate-900 outline-none transition-colors focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-2xl bg-slate-900 px-4 py-3 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {loading ? "Activating…" : "Activate Account"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
