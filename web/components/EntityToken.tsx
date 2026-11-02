"use client";

import { useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Link } from "@/lib/linker";

const KIND_LABEL = { person: "Person", org: "Organisation", project: "Project" } as const;

/** Inline mention with a sheet-style card, modelled on Granola's citation sheet. */
export function EntityToken({ text, link }: { text: string; link: Link }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const ref = useRef<HTMLButtonElement>(null);
  const id = useId();
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    // Fixed-position card in a portal so scroll containers (the transcript panel) can't clip
    // it; clamped to the viewport, and placed above the mention when there's no room below.
    const width = Math.min(352, window.innerWidth - 32);
    const below = r.bottom + 8;
    const top = below + 260 > window.innerHeight ? Math.max(16, r.top - 268) : below;
    const left = Math.min(Math.max(16, r.left), window.innerWidth - width - 16);
    setPos({ top, left });
    setOpen(true);
  };
  const ambiguous = link.status === "ambiguous";
  return (
    <span
      className="relative"
      onMouseEnter={show}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        ref={ref}
        type="button"
        aria-describedby={open ? id : undefined}
        onFocus={show}
        onBlur={() => setOpen(false)}
        onClick={() => (open ? setOpen(false) : show())}
        className={
          ambiguous
            ? "inline cursor-help rounded border-b border-dashed border-warn text-ink outline-none focus-visible:bg-warn-soft"
            : "-mx-0.5 inline cursor-help rounded px-0.5 text-left text-accent outline-none hover:bg-accent-soft focus-visible:bg-accent-soft"
        }
      >
        {text}
        {ambiguous && <sup className="ml-px text-[10px] font-semibold text-warn">?</sup>}
      </button>
      {open &&
        createPortal(
        <span
          id={id}
          role="tooltip"
          style={{ top: pos.top, left: pos.left }}
          className={`fixed z-[60] block w-[min(22rem,calc(100vw-2rem))] rounded-[22px] border border-line bg-card p-5 text-left leading-snug shadow-[0_12px_40px_rgba(31,31,29,0.12)]`}
        >
          <span className="block font-serif text-[1.6rem] leading-tight text-ink">
            {link.label}
          </span>
          <span className="mt-3 flex flex-wrap gap-1.5 text-[12px]">
            <span className="rounded-full border border-line bg-sheet px-2.5 py-0.5 text-muted">
              {KIND_LABEL[link.kind]}
            </span>
            <span
              className={`rounded-full px-2.5 py-0.5 ${
                ambiguous ? "bg-warn-soft text-warn" : "bg-accent-soft text-accent"
              }`}
            >
              {ambiguous ? "Unresolved" : `Linked · ${Math.round(link.confidence * 100)}%`}
            </span>
          </span>
          {link.detail && <span className="mt-3 block text-[13px] text-ink">{link.detail}</span>}
          {link.candidates && (
            <span className="mt-3 block space-y-1.5">
              {link.candidates.map((c) => (
                <span key={c.id} className="block rounded-xl bg-sheet px-3 py-1.5 text-[13px]">
                  <span className="text-ink">{c.name}</span>
                  <span className="text-muted"> · {c.role}</span>
                </span>
              ))}
            </span>
          )}
          <span className="mt-3 block text-[12.5px] text-muted">{link.reason}</span>
        </span>,
        document.body,
      )}
    </span>
  );
}
