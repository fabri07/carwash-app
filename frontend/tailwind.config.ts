import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

/**
 * Tokens del proyecto. La estructura viene de Véktor (escala de color por rol,
 * no por nombre de color); los valores `vektor-*` / `vk-*` se reemplazaron por
 * los de carwash.app y se sumó el preset de shadcn/ui (ADR-0010), que expone
 * cada color como variable CSS en `globals.css` para poder tener tema claro y
 * oscuro sin duplicar la paleta.
 */
const config: Config = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/features/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))",
          foreground: "hsl(var(--sidebar-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          border: "hsl(var(--sidebar-border))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      spacing: {
        // Piso de target táctil. La Fase 6 se opera de pie, con el celular y
        // las manos mojadas: 44px es el mínimo de las guías de iOS/WCAG 2.5.5.
        // Véktor usa 32-36px en el app shell; acá el token existe para que el
        // número no vuelva a quedar escrito a ojo en cada componente.
        touch: "2.75rem",
      },
      minHeight: { touch: "2.75rem" },
      minWidth: { touch: "2.75rem" },
      keyframes: {
        "fade-slide-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-slide-up": "fade-slide-up 150ms ease-out",
      },
    },
  },
  plugins: [animate],
};

export default config;
