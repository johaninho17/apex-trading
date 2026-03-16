import { Navigate, useParams } from 'react-router-dom';
import CryptoSymbolInspector from '../../components/crypto/CryptoSymbolInspector';

export default function CryptoSymbolWorkspace() {
  const { symbol } = useParams<{ symbol: string }>();

  if (!symbol) {
    return <Navigate to="/crypto/dashboard" replace />;
  }

  return <CryptoSymbolInspector symbol={symbol} mode="page" />;
}
