'use client';

interface DriftScoreProps {
  score: number; // 0-100
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
}

function getDriftColor(score: number): string {
  if (score < 30) return '#22c55e'; // green
  if (score < 60) return '#eab308'; // yellow
  if (score < 80) return '#f97316'; // orange
  return '#ef4444';                  // red
}

function getDriftLabel(score: number): string {
  if (score < 30) return 'Healthy';
  if (score < 60) return 'Moderate';
  if (score < 80) return 'Elevated';
  return 'Critical';
}

export default function DriftScore({
  score,
  size = 'md',
  showLabel = true,
}: DriftScoreProps) {
  const color = getDriftColor(score);
  const label = getDriftLabel(score);

  const sizeClasses = {
    sm: { number: 'text-2xl', label: 'text-xs' },
    md: { number: 'text-4xl', label: 'text-sm' },
    lg: { number: 'text-7xl', label: 'text-base' },
  };

  const { number: numClass, label: labelClass } = sizeClasses[size];

  const radius = size === 'lg' ? 54 : size === 'md' ? 40 : 28;
  const strokeWidth = size === 'lg' ? 6 : 4;
  const svgSize = (radius + strokeWidth) * 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative flex items-center justify-center">
        <svg
          width={svgSize}
          height={svgSize}
          style={{ transform: 'rotate(-90deg)' }}
        >
          {/* Track */}
          <circle
            cx={svgSize / 2}
            cy={svgSize / 2}
            r={radius}
            fill="none"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth={strokeWidth}
          />
          {/* Progress */}
          <circle
            cx={svgSize / 2}
            cy={svgSize / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ transition: 'stroke-dashoffset 0.6s ease' }}
          />
        </svg>

        <div
          className="absolute flex flex-col items-center"
          style={{ color }}
        >
          <span className={`font-bold leading-none ${numClass}`}>
            {score}
          </span>
          {size === 'lg' && (
            <span className="text-sm font-medium opacity-70 mt-1">/ 100</span>
          )}
        </div>
      </div>

      {showLabel && (
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{ backgroundColor: color }}
          />
          <span
            className={`font-medium ${labelClass}`}
            style={{ color }}
          >
            {label}
          </span>
        </div>
      )}
    </div>
  );
}
