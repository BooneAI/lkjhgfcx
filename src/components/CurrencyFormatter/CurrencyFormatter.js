import React from 'react';
import { isNumeric } from '../../helpers/general';
// import * as styles from './CurrencyFormatter.module.css';

const CurrencyFormatter = ({
  amount,
  currency = 'USD',
  appendZero = false,
  useDollar = false,
}) => {
  let displayAmount =
    (typeof amount !== 'number' && parseFloat(amount?.replace('$', ''))) ||
    amount;
  /* Set language display */
  const fallbackLanguage = 'en-US';
  const languageCode =
    typeof window !== 'undefined'
      ? window.navigator.language || fallbackLanguage
      : fallbackLanguage;
  const normalisedLanguage = languageCode.split('@')[0] || fallbackLanguage;

  /* Format and return */
  // isolate currency
  let formatObject;
  try {
    formatObject = new Intl.NumberFormat(normalisedLanguage, {
      style: 'currency',
      currency,
    });
  } catch (err) {
    formatObject = new Intl.NumberFormat(fallbackLanguage, {
      style: 'currency',
      currency,
    });
  }
  let symbol = '$';
  let formattedPrice = formatObject.format(displayAmount);
  if ('formatToParts' in formatObject) {
    const formattedPriceParts = formatObject.formatToParts(displayAmount);
    if (useDollar === false) symbol = formattedPriceParts[0].value;
    const currencyValue = formattedPriceParts.find(
      (obj) => obj.type === 'currency'
    );
    const decimalValue = formattedPriceParts.find(
      (obj) => obj.type === 'fraction'
    );
    formattedPrice = formattedPrice.replace(currencyValue.value, '');
    if (decimalValue && decimalValue.value === '00' && !appendZero) {
      formattedPrice = formattedPrice.replace(`.${decimalValue.value}`, '');
    }
  } else {
    // new Intl.NumberFormat is not supported; return amount with dollar sign
    formattedPrice = amount;
  }

  const priceComponent = (
    <>
      <span>{symbol}</span>
      <span>{formattedPrice}</span>
    </>
  );

  return isNumeric(amount) ? priceComponent : 'No price available';
};

export default CurrencyFormatter;
