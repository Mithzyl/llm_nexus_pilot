/** Keep asynchronous presentation updates in their originating view; persistence runs separately. */
export function guardViewUpdate<F extends (...args: never[]) => unknown>(isCurrent: () => boolean, action: F): F {
  return ((...args: Parameters<F>) => {
    if (isCurrent()) return action(...args);
  }) as F;
}
