// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - TanStack devtools (dev-only, first), tanstackStart, viteReact, tailwindcss, tsConfigPaths,
//     nitro (build-only using cloudflare as a default target), VITE_* env injection, @ path alias,
//     React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... } }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// Vite rebuilds its dependency cache the first time it discovers a package that was not in
// the initial module graph — any dependency reachable only from a lazily loaded route chunk.
// That rebuild changes the version hash of every optimized module, so a route component that
// is already on screen keeps importing the previous copy of React while the rest of the app
// uses the new one. The result is a null hook dispatcher: "Cannot read properties of null
// (reading 'useContext')" and a blank screen. Prebundling browser dependencies keeps the
// cache stable; TanStack Start must remain outside this list so its server imports can
// be removed by the framework's browser transform.
const prebundle = [
  "react",
  "react-dom",
  "@supabase/supabase-js",
  "@tanstack/react-query",
  "@tanstack/react-router",
  "class-variance-authority",
  "clsx",
  "cmdk",
  "embla-carousel-react",
  "input-otp",
  "lucide-react",
  "react-day-picker",
  "react-hook-form",
  "react-resizable-panels",
  "recharts",
  "sonner",
  "tailwind-merge",
  "vaul",
  "zod",
  "@radix-ui/react-accordion",
  "@radix-ui/react-alert-dialog",
  "@radix-ui/react-aspect-ratio",
  "@radix-ui/react-avatar",
  "@radix-ui/react-checkbox",
  "@radix-ui/react-collapsible",
  "@radix-ui/react-context-menu",
  "@radix-ui/react-dialog",
  "@radix-ui/react-dropdown-menu",
  "@radix-ui/react-hover-card",
  "@radix-ui/react-label",
  "@radix-ui/react-menubar",
  "@radix-ui/react-navigation-menu",
  "@radix-ui/react-popover",
  "@radix-ui/react-progress",
  "@radix-ui/react-radio-group",
  "@radix-ui/react-scroll-area",
  "@radix-ui/react-select",
  "@radix-ui/react-separator",
  "@radix-ui/react-slider",
  "@radix-ui/react-slot",
  "@radix-ui/react-switch",
  "@radix-ui/react-tabs",
  "@radix-ui/react-toggle",
  "@radix-ui/react-toggle-group",
  "@radix-ui/react-tooltip",
];

export default defineConfig({
  // Intranet/TrueNAS: build a standalone Node server (Docker) instead of the Cloudflare default.
  nitro: { preset: "node-server" },
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
  vite: {
    optimizeDeps: { include: prebundle },
  },
});
