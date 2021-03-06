#!/usr/bin/env python3
import argparse
import gzip
import datetime
import pysam
import sys

__author__ = 'A. Jason Grundstad'

total_reads = 0
window = 500


def read_bam(bamfile):
    global total_reads
    bam = pysam.AlignmentFile(bamfile, 'rb')
    bamiter = bam.fetch(until_eof=True)
    read1 = bamiter.__next__()
    total_reads += 1
    # read2 = None
    while read1:
        while (read1.is_secondary and not read1.is_read1) or read1.flag & 2048:
            read1 = bamiter.__next__()
            total_reads += 1
        read2 = bamiter.__next__()
        total_reads += 1
        while (read2.is_secondary and not read2.is_read2) or read2.flag & 2048:
            read2 = bamiter.__next__()
            total_reads += 1
        if read1.query_name != read2.query_name:
            logfile.write("Read 1 query not equal to read 2 query\n{}\n{}".format(read1.query_name, read2.query_name))
            raise ValueError
        yield (read1, read2)

        read1 = bamiter.__next__()
        total_reads += 1
        while read1.is_secondary:
            read1 = bamiter.__next__()
            total_reads += 1


def rev_comp(seq, qual):
    code = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C', 'N': 'N'}
    new_seq = ''
    for i in range(0, len(seq), 1):
        new_seq += code[seq[i]]
    return new_seq[::-1], qual[::-1]


def print_fastq_to_pipes(read1=None, read2=None, **kwargs):
    # sam spec is to output read in orientation of reference, will need to flip if mapped reverse complement to
    # reference
    cur_seq = read1.seq
    cur_qual = read1.qual
    if read1.is_reverse:
        (cur_seq, cur_qual) = rev_comp(read1.seq, read1.qual)
    sys.stdout.write("@{}\n{}\n+\n{}\n".format(read1.query_name, cur_seq,
                                               cur_qual))
    cur_seq = read2.seq
    cur_qual = read2.qual
    if read2.is_reverse:
        (cur_seq, cur_qual) = rev_comp(read2.seq, read2.qual)
    sys.stderr.write("@{}\n{}\n+\n{}\n".format(read2.query_name, cur_seq,
                                               cur_qual))


def print_fastq(outfile1=None, outfile2=None, read1=None, read2=None):
    # sam spec is to output read in orientation of reference, will need to flip if mapped reverse complement to
    # reference
    cur_seq = read1.seq
    cur_qual = read1.qual
    if read1.is_reverse:
        (cur_seq, cur_qual) = rev_comp(read1.seq, read1.qual)
    outfile1.write("@{}\n{}\n+\n{}\n".format(read1.query_name, cur_seq,
                                             cur_qual))
    cur_seq = read2.seq
    cur_qual = read2.qual
    if read2.is_reverse:
        (cur_seq, cur_qual) = rev_comp(read2.seq, read2.qual)
    outfile2.write("@{}\n{}\n+\n{}\n".format(read2.query_name, cur_seq,
                                             cur_qual))


def both_mapped(read1, read2):
    return not (read1.is_unmapped and read2.is_unmapped)


def mated(read1, read2, stype):
    if stype == 'DNA':
        return (read1.rname == read2.rname) and (abs(read1.isize) < window)
    else:
        return read1.rname == read2.rname


def perfect_alignments(read1, read2, mm):
    # along with cigar, also ensure edit distance is as specified
    mm = int(mm)
    try:
        # bwa-style aligner edit tag
        r1_edit = read1.get_tag('NM')
        r2_edit = read2.get_tag('NM')
    except:
        try:
            # STAR-style edit tag, nM is mismatches for the PAIR in total, for this purpose, it doesn't matter
            r1_edit = read1.get_tag('nM')
            r2_edit = read2.get_tag('nM')
        except:
            return False
    return ((len(read1.cigar) == 1 and read1.cigar[0][0] == 0) and
            (len(read2.cigar) == 1 and read2.cigar[0][0] == 0) and (r1_edit <= mm and r2_edit <= mm))


def evaluate(bam=None, fq1=None, fq2=None, mm=None, stype=None):
    pair_count = 0
    keep_count = 0
    ambiguous_count = 0
    print_it = print_fastq
    if fq1 is None:
        print_it = print_fastq_to_pipes

    for pair_count, pair in enumerate(read_bam(bam), start=1):
        # do we have alignments
        if not (pair[0].is_unmapped and pair[1].is_unmapped):
            ''' are both alignments perfect, pass it over unless:
            *  read1.reference_id != read2.reference_id
            *  NM in read.tags indicates edit distance from reference
            *  insert size: read1.isize , negative for read2
            '''
            if (both_mapped(pair[0], pair[1]) and
                    mated(pair[0], pair[1], stype) and
                    perfect_alignments(pair[0], pair[1], mm)):
                pass
            else:
                #  Consider sending imperfect alignments to other pair of files
                print_it(outfile1=fq1, outfile2=fq2, read1=pair[0], read2=pair[1])
                ambiguous_count += 1
        else:
            print_it(outfile1=fq1, outfile2=fq2, read1=pair[0], read2=pair[1])
            keep_count += 1
    return pair_count, keep_count, ambiguous_count


def main():
    desc = '''
    Detect and isolate human reads from a bam file generated from human(SEQ)
    aligned to mouse(REF).  Accepts either: a file, or sam data piped from stdin.
    NOTE: when reading from stdin, you must provide the SAM headers "@" via
    samtools' -h flag.
    '''
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('-b', dest='bam', default='-',
                        help="Input .bam (unsorted) [stdin]")
    parser.add_argument('-o', dest='output', required=False,
                        help='Output stub e.g. Human.fastq')
    parser.add_argument('-c', dest='compression', required=False, default=4,
                        type=int,
                        help='Optional fq.gz compression rate [default: 4]')
    parser.add_argument('-s', dest='sample',
                        help='Sample prefix for output summary')
    parser.add_argument('-n', dest='num_mm',
                        help='Number of allowed mismatches')
    parser.add_argument('-t', dest='stype',
                        help='RNA or DNA')
    args = parser.parse_args()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    global logfile
    logfile = open(args.sample + '.runlog.txt', 'a')
    fq1 = None
    fq2 = None
    if args.output:
        fq1 = gzip.open(args.output + '_1.fq.gz', 'w',
                        compresslevel=args.compression)
        fq2 = gzip.open(args.output + '_2.fq.gz', 'w',
                        compresslevel=args.compression)

    t_start = datetime.datetime.now()
    logfile.write("----------\nStart time: {}".format(t_start) + '\n')
    pair_count, keep_count, ambiguous_count = evaluate(
        fq1=fq1, fq2=fq2, bam=args.bam, mm=args.num_mm, stype=args.stype)
    t_end = datetime.datetime.now()
    logfile.write("End time:   {}".format(t_end) + '\n')

    time_delta = t_end - t_start
    keep_pct = (keep_count + 0.0) / pair_count * 100
    ambig_pct = (ambiguous_count + 0.0) / pair_count * 100
    global total_reads
    logfile.write("{} total reads".format(total_reads) + '\n')
    logfile.write("kept {} alignment pairs out of {}  {:.4f}%".format(keep_count, pair_count, keep_pct) + '\n')
    logfile.write("kept {} ambiguous alignment pairs out of {}  {:.4f}%".format(ambiguous_count, pair_count, ambig_pct) + '\n')
    logfile.write("time delta: {}".format(str(time_delta)) + '\n')
    return 0


if __name__ == '__main__':
    main()
