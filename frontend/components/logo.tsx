import Link from "next/link";

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="inline-flex items-center gap-2.5 font-bold tracking-tight text-ink">
      <span className="grid h-9 w-9 place-items-center rounded-xl bg-ink text-sm text-white">F</span>
      {!compact && <span>FlowMind</span>}
    </Link>
  );
}

