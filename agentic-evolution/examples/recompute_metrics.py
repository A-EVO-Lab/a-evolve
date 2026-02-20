"""
Recompute metrics for existing evaluation files using fixed extraction logic.
This script reads evaluation JSON files and recomputes metrics without re-running inference.
"""

import sys
import os
import json
import argparse
from datetime import datetime

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from examples.run_example import (
    extract_prediction_from_output, 
    parse_prediction_fields, 
    normalize_prediction,
    calculate_field_accuracy
)


def recompute_sample_metrics(sample):
    """
    Recompute metrics for a single sample using fixed extraction logic.
    
    Args:
        sample: dict with 'raw_output' and 'ground_truth' fields
        
    Returns:
        Updated sample dict with corrected metrics
    """
    # Extract prediction using fixed logic
    raw_output = sample['raw_output']
    extracted_output = extract_prediction_from_output(raw_output)
    
    # Normalize both prediction and ground truth
    normalized_prediction = normalize_prediction(extracted_output)
    normalized_ground_truth = normalize_prediction(sample['ground_truth'].strip())
    
    # Check if prediction matches answer
    is_correct = normalized_prediction == normalized_ground_truth
    
    # Parse fields for detailed analysis
    pred_fields = parse_prediction_fields(normalized_prediction)
    gt_fields = parse_prediction_fields(normalized_ground_truth)
    
    # Check event type match based on parsed fields (FIXED)
    event_type_match = pred_fields.get('event_name', '') == gt_fields.get('event_name', '')
    
    # Update sample with corrected values
    sample['extracted_prediction'] = extracted_output
    sample['prediction'] = normalized_prediction
    sample['correct'] = is_correct
    sample['event_type_match'] = event_type_match
    sample['predicted_fields'] = pred_fields
    sample['ground_truth_fields'] = gt_fields
    
    # Update target_event_type from ground truth (should be consistent)
    sample['target_event_type'] = gt_fields.get('event_name', 'unknown')
    
    return sample


def recompute_overall_metrics(results):
    """
    Recompute overall metrics from results.
    
    Args:
        results: List of sample results
        
    Returns:
        dict with overall metrics
    """
    correct_count = sum(1 for r in results if r.get('correct', False))
    event_type_correct = sum(1 for r in results if r.get('event_type_match', False))
    
    total = len(results)
    accuracy = correct_count / total * 100 if total else 0
    event_type_accuracy = event_type_correct / total * 100 if total else 0
    
    return {
        'exact_match_count': correct_count,
        'exact_match_accuracy': round(accuracy, 2),
        'event_type_match_count': event_type_correct,
        'event_type_match_accuracy': round(event_type_accuracy, 2)
    }


def recompute_evaluation_file(input_path, output_path=None):
    """
    Recompute metrics for an entire evaluation file.
    
    Args:
        input_path: Path to input evaluation JSON
        output_path: Path to output JSON (if None, overwrites input)
    """
    print(f"\nProcessing: {input_path}")
    print("="*80)
    
    # Load existing evaluation
    with open(input_path, 'r') as f:
        data = json.load(f)
    
    original_overall = data['overall_metrics'].copy()
    original_field = data['field_level_metrics']['accuracy'].copy()
    
    # Recompute metrics for each sample
    print(f"Recomputing metrics for {len(data['detailed_results'])} samples...")
    
    changes = []
    for i, sample in enumerate(data['detailed_results']):
        old_extracted = sample.get('extracted_prediction', '')
        old_event_match = sample.get('event_type_match', False)
        
        # Recompute
        recompute_sample_metrics(sample)
        
        # Track changes
        if old_extracted != sample['extracted_prediction'] or old_event_match != sample['event_type_match']:
            changes.append({
                'id': sample['id'],
                'old_extracted': old_extracted,
                'new_extracted': sample['extracted_prediction'],
                'old_event_match': old_event_match,
                'new_event_match': sample['event_type_match']
            })
    
    # Recompute overall metrics
    data['overall_metrics'] = recompute_overall_metrics(data['detailed_results'])
    
    # Recompute field-level accuracy
    field_accuracy, field_stats = calculate_field_accuracy(data['detailed_results'])
    data['field_level_metrics']['accuracy'] = {field: round(acc, 2) for field, acc in field_accuracy.items()}
    data['field_level_metrics']['non_null_counts'] = field_stats['non_null_counts']
    
    # Update timestamp
    data['evaluation_info']['recomputed_timestamp'] = datetime.now().isoformat()
    data['evaluation_info']['recomputed_note'] = 'Metrics recomputed with fixed extraction and event_type_match logic'
    
    # Save results
    if output_path is None:
        output_path = input_path
    
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    
    # Print summary
    print(f"\n✅ Recomputed metrics saved to: {output_path}")
    print("\n" + "="*80)
    print("METRICS COMPARISON:")
    print("="*80)
    
    print("\nOverall Metrics:")
    print(f"  Exact Match Accuracy:")
    print(f"    Before: {original_overall['exact_match_accuracy']}%")
    print(f"    After:  {data['overall_metrics']['exact_match_accuracy']}%")
    print(f"    Change: {data['overall_metrics']['exact_match_accuracy'] - original_overall['exact_match_accuracy']:+.2f}%")
    
    print(f"\n  Event Type Match Accuracy:")
    print(f"    Before: {original_overall['event_type_match_accuracy']}%")
    print(f"    After:  {data['overall_metrics']['event_type_match_accuracy']}%")
    print(f"    Change: {data['overall_metrics']['event_type_match_accuracy'] - original_overall['event_type_match_accuracy']:+.2f}%")
    
    print(f"\nField-Level Accuracy (event_name):")
    print(f"  Before: {original_field['event_name']}%")
    print(f"  After:  {data['field_level_metrics']['accuracy']['event_name']}%")
    print(f"  Change: {data['field_level_metrics']['accuracy']['event_name'] - original_field['event_name']:+.2f}%")
    
    print(f"\n✅ Consistency Check:")
    event_type_acc = data['overall_metrics']['event_type_match_accuracy']
    field_event_acc = data['field_level_metrics']['accuracy']['event_name']
    print(f"  Event Type Match Accuracy: {event_type_acc}%")
    print(f"  Field-Level event_name Accuracy: {field_event_acc}%")
    if event_type_acc == field_event_acc:
        print(f"  ✅ CONSISTENT! (Both are {event_type_acc}%)")
    else:
        print(f"  ⚠️ INCONSISTENT! Difference: {abs(event_type_acc - field_event_acc):.2f}%")
    
    if changes:
        print(f"\n📝 Changed Samples: {len(changes)}")
        print("\nFirst 5 changes:")
        for change in changes[:5]:
            print(f"\n  {change['id']}:")
            if change['old_extracted'] != change['new_extracted']:
                print(f"    Extraction changed:")
                print(f"      Old: {change['old_extracted'][:80]}...")
                print(f"      New: {change['new_extracted'][:80]}...")
            if change['old_event_match'] != change['new_event_match']:
                print(f"    Event match: {change['old_event_match']} → {change['new_event_match']}")
    else:
        print("\n✅ No changes (already using correct logic)")
    
    print("\n" + "="*80)
    return data


def main():
    parser = argparse.ArgumentParser(
        description='Recompute metrics for evaluation files with fixed extraction logic'
    )
    parser.add_argument(
        'input_files',
        nargs='+',
        help='Path(s) to evaluation JSON file(s) to recompute'
    )
    parser.add_argument(
        '--output-suffix',
        default='_fixed',
        help='Suffix for output files (default: _fixed). Use empty string to overwrite.'
    )
    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Overwrite input files instead of creating new ones'
    )
    
    args = parser.parse_args()
    
    print("="*80)
    print("RECOMPUTING EVALUATION METRICS")
    print("="*80)
    print(f"Files to process: {len(args.input_files)}")
    
    for input_path in args.input_files:
        if not os.path.exists(input_path):
            print(f"\n❌ File not found: {input_path}")
            continue
        
        # Determine output path
        if args.overwrite:
            output_path = input_path
        elif args.output_suffix:
            base, ext = os.path.splitext(input_path)
            output_path = f"{base}{args.output_suffix}{ext}"
        else:
            output_path = input_path
        
        try:
            recompute_evaluation_file(input_path, output_path)
        except Exception as e:
            print(f"\n❌ Error processing {input_path}: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "="*80)
    print("✅ All files processed!")
    print("="*80)


if __name__ == "__main__":
    main()

