import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
  site: "https://orfrederick.github.io",
  base: "/SepsisAtlas",
  trailingSlash: "always",
  build: {
    format: "directory",
  },
});
