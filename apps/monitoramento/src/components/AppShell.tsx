import { Link, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Radio, CalendarRange, Building2, Users, Moon, Sun, LogOut, SlidersHorizontal } from "lucide-react";
import { COMPANY_LOGO_URL } from '@/lib/branding';
import { supabase } from "@/integrations/supabase/client";
import { useActiveShift, useMe, useOperators, useOperatorAccess } from "@/hooks/use-safenet";
import { canOperate } from '@/lib/access';
import { Button } from "@/components/ui/button";

const NAV = [
  { to: "/plantao", label: "Plantão", icon: Radio },
  { to: "/relatorio", label: "Relatório semanal", icon: CalendarRange },
  { to: "/clientes", label: "Clientes", icon: Building2 },
  { to: "/operadores", label: "Operadores", icon: Users },
  { to: "/campos", label: "Campos", icon: SlidersHorizontal },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { data: status, isPending, isError, refetch } = useOperatorAccess();
  const qc = useQueryClient();
  useEffect(() => { if (status && !canOperate(status)) { void qc.cancelQueries({ predicate: q => q.queryKey[0] !== 'operator-access' }); qc.removeQueries({ predicate: q => q.queryKey[0] !== 'operator-access' }); } }, [status, qc]);
  if (isPending) return <main className="p-6">Verificando autorização…</main>;
  if (!canOperate(status)) return <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background p-6 text-center"><h1 className="text-2xl font-bold">Monitoramento Safenet</h1><p>{isError ? 'Não foi possível verificar sua autorização.' : status === 'removed' ? 'Seu acesso foi removido.' : 'Seu cadastro aguarda aprovação do administrador.'}</p><Button variant="outline" onClick={() => refetch()}>Verificar autorização</Button><Button variant="ghost" onClick={async () => { await supabase.auth.signOut(); window.location.assign('/auth'); }}>Sair</Button></main>;
  return <ApprovedShell>{children}</ApprovedShell>;
}

function ApprovedShell({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const { data: shift } = useActiveShift();
  const { data: ops } = useOperators(true);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [dark, setDark] = useState(false);
  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);

  const toggle = () => {
    const d = !dark;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    localStorage.setItem("theme", d ? "dark" : "light");
  };

  const signOut = async () => {
    await qc.cancelQueries();
    qc.clear();
    await supabase.auth.signOut();
    navigate({ to: "/auth", replace: true });
  };

  const onDuty = shift ? ops?.find((o) => o.id === shift.operator_id)?.name : null;

  return (
    <div className="flex min-h-screen bg-background">
      <aside className="no-print hidden w-60 shrink-0 flex-col bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex items-center gap-2 border-b border-sidebar-border px-5 py-5">
          <img src={COMPANY_LOGO_URL} alt="Logo Safenet" className="h-7 w-7 shrink-0 object-contain" />
          <div className="leading-tight">
            <div className="text-sm font-bold tracking-wide">SAFENET</div>
            <div className="text-xs opacity-70">Monitoramento</div>
          </div>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              className="flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium opacity-80 hover:bg-sidebar-accent hover:opacity-100"
              activeProps={{ className: "bg-sidebar-accent !opacity-100" }}
            >
              <n.icon className="h-4 w-4" /> {n.label}
            </Link>
          ))}
        </nav>
        <div className="border-t border-sidebar-border p-4 text-xs">
          <div className="mb-1 uppercase tracking-wider opacity-60">De plantão</div>
          <div className="flex items-center gap-2 font-semibold">
            <span className={`h-2 w-2 rounded-full ${shift ? "bg-success" : "bg-sidebar-border"}`} />
            {onDuty ? `${onDuty} · ${shift?.shift_type}` : "Nenhum plantão aberto"}
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print flex items-center justify-between gap-2 border-b bg-card px-4 py-3">
          <nav className="flex gap-1 md:hidden">
            {NAV.map((n) => (
              <Link key={n.to} to={n.to} className="rounded p-2 text-muted-foreground" activeProps={{ className: "bg-accent !text-foreground" }}>
                <n.icon className="h-4 w-4" />
              </Link>
            ))}
          </nav>
          <div className="hidden text-sm text-muted-foreground md:block">
            Operador: <span className="font-semibold text-foreground">{me?.profile.name}</span>
            {me?.isAdmin && <span className="ml-2 rounded bg-accent px-1.5 py-0.5 text-xs text-accent-foreground">admin</span>}
          </div>
          <div className="flex gap-1">
            <Button variant="ghost" size="icon" onClick={toggle} aria-label="Alternar tema">
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
            <Button variant="ghost" size="sm" onClick={signOut}>
              <LogOut className="mr-1 h-4 w-4" /> Sair
            </Button>
          </div>
        </header>
        <main className="print-area flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
