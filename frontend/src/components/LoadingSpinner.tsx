import { Loader2 } from 'lucide-react'

interface LoadingSpinnerProps {
  text?: string
  className?: string
}

export function LoadingSpinner({ text = 'Loading...', className = '' }: LoadingSpinnerProps) {
  return (
    <div className={`flex items-center justify-center py-4 ${className}`}>
      <Loader2 className="w-6 h-6 text-primary animate-spin" />
      <span className="ml-2 text-content-muted">{text}</span>
    </div>
  )
}

