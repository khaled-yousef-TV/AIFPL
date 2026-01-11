import { Loader2 } from 'lucide-react'

interface LoadingSpinnerProps {
  text?: string
  className?: string
}

export function LoadingSpinner({ text = 'Loading...', className = '' }: LoadingSpinnerProps) {
  return (
    <div className={`flex items-center justify-center py-4 ${className}`}>
      <Loader2 className="w-6 h-6 text-[#00ff87] animate-spin" />
      <span className="ml-2 text-gray-400">{text}</span>
    </div>
  )
}

