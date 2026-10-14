import { createFileRoute, Link } from "@tanstack/react-router";
import { ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { title: "Monitoramento Safenet — Plantão e relatórios" },
      { name: "description", content: "Registro de plantão, armes, desarmes, disparos e relatórios semanais da Safenet." },
      { property: "og:title", content: "Monitoramento Safenet" },
      { property: "og:description", content: "Registro de plantão e relatórios semanais da equipe de monitoramento." },
    ],
  }),
  component: Home,
});

function Home() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-sidebar p-6 text-center text-sidebar-foreground">
      <ShieldCheck className="h-16 w-16" />
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Monitoramento Safenet</h1>
        <p className="mt-2 max-w-md opacity-80">Acompanhamento de plantão, passagem de turno e relatórios semanais por cliente.</p>
      </div>
      <Button asChild size="lg" variant="secondary"><Link to="/plantao">Acessar painel</Link></Button>
    </div>
  );
}
