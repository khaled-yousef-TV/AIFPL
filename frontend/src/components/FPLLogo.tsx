interface FPLLogoProps {
  className?: string
}

export function FPLLogo({ className }: FPLLogoProps) {
  return (
    <svg 
      viewBox="0 0 40 40" 
      className={className}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Trophy base */}
      <path 
        d="M20 8C18.5 8 17.5 9 17.5 10.5V12H14C13.4 12 13 12.4 13 13V17C13 19.8 15.2 22 18 22H22C24.8 22 27 19.8 27 17V13C27 12.4 26.6 12 26 12H22.5V10.5C22.5 9 21.5 8 20 8Z" 
        fill="currentColor"
        className="text-[#FFD700]"
      />
      {/* Trophy cup */}
      <path 
        d="M20 24C16.7 24 14 21.3 14 18V16H26V18C26 21.3 23.3 24 20 24Z" 
        fill="currentColor"
        className="text-[#FFA500]"
      />
      {/* Star on trophy */}
      <path 
        d="M20 14L20.9 16.5L23.5 16.8L21.5 18.5L22.2 21L20 19.5L17.8 21L18.5 18.5L16.5 16.8L19.1 16.5L20 14Z" 
        fill="currentColor"
        className="text-[#FFD700]"
      />
      {/* FPL letters */}
      <text 
        x="20" 
        y="32" 
        textAnchor="middle" 
        className="text-[8px] font-bold fill-white"
        fontFamily="Arial, sans-serif"
        fontWeight="bold"
      >
        FPL
      </text>
    </svg>
  )
}

