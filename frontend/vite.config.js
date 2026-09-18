import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 5173 and 8000 are in use by another project on this machine.
export default defineConfig({
  plugins: [react()],
  server: { port: 5180, strictPort: true },
});
