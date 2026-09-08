"use client";

interface NumberInputProps {
  value: number | null | undefined;
  onChange: (value: number) => void;
  placeholder?: string;
  maxLength?: number;
  max?: number;
  className?: string;
  /** Horizontal padding inside the brackets; "0.5ch" for dense rows. */
  padding?: string;
}

function parseNum(v: string): number {
  const n = parseInt(v.replace(/[^0-9]/g, ""), 10);
  return isNaN(n) ? 0 : n;
}

export function NumberInput({
  value,
  onChange,
  placeholder = "0",
  maxLength = 2,
  max = 99,
  className = "",
  padding = "1ch",
}: NumberInputProps) {
  return (
    <input
      type="text"
      inputMode="numeric"
      value={value != null && value > 0 ? String(value) : ""}
      onChange={(e) => {
        const n = parseNum(e.target.value);
        if (n <= max) onChange(n);
      }}
      placeholder={placeholder}
      maxLength={maxLength}
      style={{ width: `${maxLength}ch`, paddingLeft: padding, paddingRight: padding, boxSizing: "content-box" }}
      className={`bg-transparent text-base05 outline-none font-mono placeholder-base04 text-center ${className}`}
    />
  );
}
