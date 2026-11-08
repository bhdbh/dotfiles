use rink_core::{
    ast::Query,
    output::{Digits, NumberParts, QueryReply},
    types::{BigInt, Numeric},
    Context,
};

// Keep results useful in a single text field, including when reused in a calculation.
const DECIMAL_PLACES: u32 = 6;

pub(super) fn calculate(
    ctx: &mut Context,
    input: &str,
) -> Result<(String, Option<String>), String> {
    // An explicitly requested fraction, base or precision still takes precedence.
    let explicit_format = matches!(
        rink_core::reformat(ctx, input),
        Query::Convert(_, _, base, digits) if base.is_some() || digits != Digits::Default
    );
    let reply = rink_core::eval(ctx, input).map_err(|error| error.to_string())?;
    let result = if explicit_format {
        return Ok(super::parse_result(reply.to_string()));
    } else {
        match reply {
            QueryReply::Number(parts) => number(ctx, parts, true),
            QueryReply::Conversion(reply) => number(ctx, reply.value, false),
            QueryReply::Def(reply) if reply.value.is_some() => number(ctx, reply.value.unwrap(), true),
            QueryReply::UnitList(reply) => reply.list.into_iter().map(|part| number(ctx, part, true)).collect::<Vec<_>>().join(", "),
            QueryReply::Duration(reply) => [
                reply.years, reply.months, reply.weeks, reply.days,
                reply.hours, reply.minutes,
            ].into_iter()
                .filter(|part| part.exact_value.as_deref() != Some("0"))
                .chain([reply.seconds])
                .map(|part| number(ctx, part, true)).collect::<Vec<_>>().join(", "),
            other => return Ok(super::parse_result(other.to_string())),
        }
    };
    Ok((result, None))
}

fn number(ctx: &Context, mut parts: NumberParts, prettify: bool) -> String {
    if let Some(raw) = &parts.raw_value {
        let value = if prettify { raw.prettify(ctx).value } else { raw.value.clone() };
        parts.exact_value = Some(decimal(&value));
    } else if parts.exact_value.is_none() {
        parts.exact_value = parts.approx_value.take();
    }
    // Units remain visible; quantity labels and alternate exact forms add clutter.
    parts.format("e u")
}

fn decimal(value: &Numeric) -> String {
    if let Numeric::Float(value) = value {
        if !value.is_finite() {
            return value.to_string();
        }
    }
    let (numerator, denominator) = value.to_rational();
    let negative = numerator < BigInt::zero();
    let mut numerator = numerator.abs();
    let mut denominator = denominator.abs();
    let ten = BigInt::from(10);
    let scale = ten.pow(DECIMAL_PLACES);
    let scientific = numerator != BigInt::zero()
        && (numerator >= &denominator * &ten.pow(12) || &numerator * &scale < denominator);
    let mut exponent = 0;
    if scientific {
        exponent = numerator.to_string().len() as i32 - denominator.to_string().len() as i32;
        let below_power = if exponent >= 0 {
            numerator < &denominator * &ten.pow(exponent as u32)
        } else {
            &numerator * &ten.pow((-exponent) as u32) < denominator
        };
        if below_power {
            exponent -= 1;
        }
        if exponent >= 0 {
            denominator = &denominator * &ten.pow(exponent as u32);
        } else {
            numerator = &numerator * &ten.pow((-exponent) as u32);
        }
    }
    let scaled = &numerator * &scale;
    let mut rounded = &scaled / &denominator;
    if &(&scaled % &denominator) * &BigInt::from(2) >= denominator {
        rounded = &rounded + &BigInt::one();
    }
    let mut result = format!("{:0>width$}", rounded.to_string(), width = DECIMAL_PLACES as usize + 1);
    result.insert(result.len() - DECIMAL_PLACES as usize, '.');
    result = result.trim_end_matches('0').trim_end_matches('.').to_owned();
    if scientific {
        if result == "10" {
            result = "1".to_owned();
            exponent += 1;
        }
        result.push_str(&format!("e{exponent}"));
    }
    if negative && result != "0" {
        result.insert(0, '-');
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn everyday_arithmetic_rounds_without_recurring_or_fraction_notation() {
        let mut ctx = rink_core::simple_context().unwrap();
        for (input, expected) in [
            ("16/3", "5.333333"), ("2/3", "0.666667"),
            ("1/7", "0.142857"), ("-2/3", "-0.666667"),
            ("2+2", "4"), ("0.1+0.2", "0.3"), ("300*.1", "30"),
            ("1/8", "0.125"), ("1.9999999", "2"),
            ("1.2345675", "1.234568"), ("-1.2345675", "-1.234568"),
            ("-0.0000011", "-0.000001"),
            ("sqrt(2)", "1.414214"), ("sqrt(81)", "9"),
            ("2^10", "1024"), ("200 * 15%", "30"),
            ("pi", "3.141593"), ("sin(pi/2)", "1"),
        ] {
            assert_eq!(calculate(&mut ctx, input).unwrap(), (expected.into(), None), "{input}");
        }
    }

    #[test]
    fn conversions_keep_the_target_unit_and_support_continuing_a_result() {
        let mut ctx = rink_core::simple_context().unwrap();
        for (input, expected) in [
            ("2 m + 3 m", "5 meter"),
            ("1000 meter", "1 kilometer"),
            ("0.001 meter", "1 millimeter"),
            ("1 kg", "1 kilogram"),
            ("0 second", "0 second"),
            ("1 meter to cm", "100 centimeter"),
            ("1 meter / 3 to cm", "33.333333 centimeter"),
            ("5.333333 * 3", "15.999999"),
        ] {
            assert_eq!(calculate(&mut ctx, input).unwrap(), (expected.into(), None), "{input}");
        }
        for input in ["20 degC to degF", "90 min to hour; min"] {
            let (result, _) = calculate(&mut ctx, input).unwrap();
            assert!(!result.is_empty() && !result.contains(['[', '\n']), "{input}: {result}");
        }
        assert!(calculate(&mut ctx, "1/0").is_err());
        assert!(calculate(&mut ctx, "2+").is_err());
    }

    #[test]
    fn extreme_values_remain_nonzero_and_round_without_float_precision_loss() {
        let mut ctx = rink_core::simple_context().unwrap();
        for (input, expected) in [
            ("1/10000000", "1e-7"), ("-2/30000000", "-6.666667e-8"),
            ("10^30", "1e30"), ("999999999999.9999999", "1000000000000"),
            ("999999999999999999999999999999/10^30", "1"),
        ] {
            assert_eq!(calculate(&mut ctx, input).unwrap().0, expected, "{input}");
        }
    }

    #[test]
    fn explicit_single_line_output_formats_still_work() {
        let mut ctx = rink_core::simple_context().unwrap();
        for input in ["16/3 to fraction", "255 to hex", "1/11 to digits 10"] {
            let expected = super::super::parse_result(rink_core::one_line(&mut ctx, input).unwrap());
            assert_eq!(calculate(&mut ctx, input).unwrap(), expected, "{input}");
        }
    }
}
