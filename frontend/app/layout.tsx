import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = { title: "Markwise — Brand governance", description: "Pre-publication brand governance that explains, cites, and suggests." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
