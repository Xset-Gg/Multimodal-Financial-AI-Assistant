export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight ${className}`}>
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect x="1" y="1" width="30" height="30" rx="8" fill="#12151b" stroke="rgba(255,255,255,0.14)" />
        <circle cx="14" cy="14" r="6.5" fill="none" stroke="#3987e5" strokeWidth="2.5" />
        <path d="M18.8 18.8 24 24" stroke="#3987e5" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M11 15.5 13 13l1.8 1.8L17 12" fill="none" stroke="#f4f5f7" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span>
        SpendLens <span className="text-accent-ink">AI</span>
      </span>
    </span>
  );
}
