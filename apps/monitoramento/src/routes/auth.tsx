import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { z } from "zod";
import { toast } from "sonner";
import { COMPANY_LOGO_URL } from '@/lib/branding';
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/auth")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Entrar — Monitoramento Safenet" },
      { name: "description", content: "Acesso dos operadores de monitoramento Safenet." },
      { property: "og:title", content: "Entrar — Monitoramento Safenet" },
      { property: "og:description", content: "Acesso dos operadores de monitoramento Safenet." },
    ],
  }),
  component: AuthPage,
});

const schema = z.object({
  email: z.string().trim().email("E-mail inválido").max(255),
  password: z.string().min(6, "Mínimo 6 caracteres").max(72),
  name: z.string().trim().max(100).optional(),
});

function AuthPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = schema.safeParse({ email, password, name });
    if (!parsed.success) { toast.error(parsed.error.issues[0]?.message ?? "Dados inválidos"); return; }
    if (mode === "up" && !name.trim()) { toast.error("Informe seu nome"); return; }
    setBusy(true);
    if (mode === "in") {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      setBusy(false);
      if (error) { toast.error("E-mail ou senha incorretos"); return; }
      navigate({ to: "/plantao" });
    } else {
      const { data, error } = await supabase.auth.signUp({
        email, password, options: { data: { name: name.trim() } },
      });
      setBusy(false);
      if (error) { toast.error(error.message); return; }
      toast.success("Cadastro enviado. O acesso depende da aprovação do administrador.");
      if (data.session) { navigate({ to: "/plantao" }); return; }
      setMode("in");
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-sidebar p-4">
      <div className="w-full max-w-sm rounded-xl bg-card p-8 shadow-2xl">
        <div className="mb-6 flex items-center gap-3">
          <img src={COMPANY_LOGO_URL} alt="Logo Safenet" className="h-9 w-9 shrink-0 object-contain" />
          <div>
            <h1 className="text-lg font-bold">Monitoramento Safenet</h1>
            <p className="text-sm text-muted-foreground">{mode === "in" ? "Entrar como operador" : "Cadastrar operador"}</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          {mode === "up" && (
            <div className="space-y-1.5"><Label htmlFor="auth-name">Nome do operador</Label><Input id="auth-name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></div>
          )}
          <div className="space-y-1.5"><Label htmlFor="auth-email">E-mail</Label><Input id="auth-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          <div className="space-y-1.5"><Label htmlFor="auth-password">Senha</Label><Input id="auth-password" type="password" autoComplete={mode === "in" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} /></div>
          <Button className="w-full" disabled={busy}>{mode === "in" ? "Entrar" : "Cadastrar"}</Button>
        </form>
        <button className="mt-4 w-full text-center text-sm text-muted-foreground hover:text-foreground" onClick={() => setMode(mode === "in" ? "up" : "in")}>
          {mode === "in" ? "Novo operador? Cadastre-se" : "Já tem conta? Entrar"}
        </button>
      </div>
    </div>
  );
}
