// Cliente Supabase para a instalação na intranet (Supabase self-hosted no TrueNAS).
//
// - Navegador: fala com a MESMA origem do app. O nginx do stack encaminha /auth/v1 e /rest/v1
//   para o GoTrue e o PostgREST. Só use VITE_SUPABASE_URL se a API estiver em outro endereço (URL absoluta).
// - Servidor (SSR / server functions): usa SUPABASE_URL, o endereço interno da rede Docker.
// - Chave "anon" (pública): no navegador vem de /config.js (gerado pelo nginx a partir do .env, então a imagem
//   do app é igual em qualquer instalação); no servidor vem de SUPABASE_PUBLISHABLE_KEY. VITE_SUPABASE_PUBLISHABLE_KEY
//   é só um fallback para desenvolvimento local (vite dev).
import { createClient } from '@supabase/supabase-js';
import type { Database } from './types';

declare global {
  interface Window {
    __SAFENET__?: { key?: string };
  }
}

function resolveSupabaseUrl(): string | undefined {
  if (typeof window !== 'undefined') {
    const configured = import.meta.env['VITE_SUPABASE_URL'] as string | undefined;
    return configured && /^https?:\/\//i.test(configured) ? configured.replace(/\/+$/, '') : window.location.origin;
  }
  return process.env['SUPABASE_URL']?.replace(/\/+$/, '') || (import.meta.env['VITE_SUPABASE_URL'] as string | undefined);
}

function createSupabaseClient() {
  const SUPABASE_URL = resolveSupabaseUrl();
  const SUPABASE_PUBLISHABLE_KEY =
    typeof window !== 'undefined'
      ? window.__SAFENET__?.key || (import.meta.env['VITE_SUPABASE_PUBLISHABLE_KEY'] as string | undefined)
      : process.env['SUPABASE_PUBLISHABLE_KEY'] || (import.meta.env['VITE_SUPABASE_PUBLISHABLE_KEY'] as string | undefined);

  if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
    const missing = [
      ...(!SUPABASE_URL ? ['SUPABASE_URL'] : []),
      ...(!SUPABASE_PUBLISHABLE_KEY ? ['SUPABASE_PUBLISHABLE_KEY'] : []),
    ];
    const message = `Variável(is) de ambiente do Supabase ausente(s): ${missing.join(', ')}. Veja o .env do stack.`;
    console.error(`[Supabase] ${message}`);
    throw new Error(message);
  }

  return createClient<Database>(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
    },
  });
}

let _supabase: ReturnType<typeof createSupabaseClient> | undefined;

// Import the supabase client like this:
// import { supabase } from "@/integrations/supabase/client";
export const supabase = new Proxy({} as ReturnType<typeof createSupabaseClient>, {
  get(_, prop, receiver) {
    if (!_supabase) _supabase = createSupabaseClient();
    return Reflect.get(_supabase, prop, receiver);
  },
});
