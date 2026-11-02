/** Renders Granola's summary_markdown subset (### headings, - bullets, **bold**) the way
 *  Granola shows enhanced notes: bold sans section headings, plain ink bullets. */
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") ? (
      <strong key={i} className="font-semibold text-ink">
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    ),
  );
}

export function Markdownish({ source }: { source: string }) {
  const blocks: React.ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      blocks.push(
        <ul key={blocks.length} className="list-disc space-y-1.5 pl-5 marker:text-ink/35">
          {bullets.map((b, i) => (
            <li key={i} className="pl-1">
              {inline(b)}
            </li>
          ))}
        </ul>,
      );
      bullets = [];
    }
  };
  for (const line of source.split("\n")) {
    if (line.startsWith("### ")) {
      flush();
      blocks.push(
        <h3
          key={blocks.length}
          className="mb-2 mt-7 text-[17px] font-semibold tracking-[-0.005em] text-ink first:mt-0"
        >
          {line.slice(4)}
        </h3>,
      );
    } else if (line.startsWith("- ")) bullets.push(line.slice(2));
    else if (line.trim()) {
      flush();
      blocks.push(
        <p key={blocks.length} className="mb-3">
          {inline(line)}
        </p>,
      );
    }
  }
  flush();
  return <div className="text-[15.5px] leading-[1.65] text-ink/90">{blocks}</div>;
}

/** The user's own typed notes: plain lines, as typed. */
export function TypedNotes({ source }: { source: string }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-[15.5px] leading-[1.65] text-ink marker:text-ink/35">
      {source
        .split("\n")
        .filter((l) => l.trim())
        .map((l, i) => (
          <li key={i} className="pl-1">
            {l}
          </li>
        ))}
    </ul>
  );
}
