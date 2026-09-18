import type { Config } from "jest";
import nextJest from "next/jest.js";

const createJestConfig = nextJest({ dir: "./" });

const config: Config = {
  testEnvironment: "jsdom",
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  modulePathIgnorePatterns: ["<rootDir>/.next/"],
  collectCoverageFrom: [
    "src/**/*.{ts,tsx}",
    "public/sw.js",
    // Vendorizado de shadcn/ui (ADR-0010): código de terceros con su propia
    // batería de pruebas; medirlo acá inflaría o hundiría el número sin decir
    // nada de nuestro código.
    "!src/components/ui/{button,input,label,form,card,sonner,dropdown-menu,sheet}.tsx",
    // Generados / sin comportamiento.
    "!src/types/**",
    "!src/test/**",
    "!src/**/*.d.ts",
    // Configuración de Sentry por runtime: solo llama a `Sentry.init`.
    "!src/instrumentation*.ts",
  ],
  // ADR-0008: piso único de 80% en las cuatro métricas. El número vive solo acá.
  coverageThreshold: {
    global: { statements: 80, branches: 80, functions: 80, lines: 80 },
  },
};

export default createJestConfig(config);
